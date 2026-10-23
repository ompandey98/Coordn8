#!/usr/bin/env python3
"""
Coordn8 gate — EXPLANATION CRAFT (advisory): does an explanation have the shape its
subject needs, fence its analogies, end where the reader can act, and talk to its
audience at the right level? It REPORTS; it never rewrites (the input is read-only).

WHAT IT MEASURES (every rule is a pattern over the text — no model judgement)
  I2  shape: the ordered parts of the chosen shape (data/explain_shapes.json) —
        error:        quotes the one meaningful line → translates it → names the cause →
                      says what to change; two likely causes → both + how to tell apart
        function:     its job in ONE first sentence → 2–4 parts in run order → says what
                      boilerplate was skipped (with --code, the named parts must appear in
                      the code in the same order, and every named identifier must exist)
        architecture: ONE path (a single request/record) traced hop by hop to its end;
                      each piece named by what it does, not just what it is called
        concept:      what it is → analogy → details → so what
        + term / how-it-works / design-question / decision-brief, and any caller-invented
        shape (`--shape invented:<name> --parts "id=cue|cue;id2=cue"`) — accepted, never
        refused for not being in the catalog
  I3  every analogy (like / think of / imagine / as if / similar to …) is followed within
      two sentences by where it stops being true (unlike / except / the difference …);
      two examples for one point; every simplification marker is reported as a trade-off
      (with --code, an example that names nothing from the code is flagged)
  I4  the last sentence of every section (and of the narration) is something the reader
      can now DO; a caveat with no real cost (money, data, security, irreversible,
      outage …) is flagged as unneeded; when content was cut (--cut) the text says so
  I5  for an audience expert in the field: a lay paraphrase of a field term is talking
      down; for a non-expert audience: jargon is defined in the same breath on first use
  I1  business audiences: impact first and a decision ("this means we should …");
      purpose before mechanism; lead with the answer (no throat-clearing opener)
  length follows the question (--question): a "what does X mean" question gets a short,
      heading-free answer

NOT JUDGED (needs a reader): whether a simplification is false, whether an analogy fits,
whether the chosen steps are the ones that matter, whether any claim is true.

Usage:
    python3 scripts/gates/check_explain.py explanation.md --shape error --audience engineer
    python3 scripts/gates/check_explain.py page_sections.json --shape concept --audience brief.json
    python3 scripts/gates/check_explain.py narration.json --cut "retry policy,metrics"
    python3 scripts/gates/check_explain.py a.md --shape function --code src/handler.py
Input: .md / .txt / .html, or JSON (a list of lines, {"lines"|"beats"|"scenes": [...]},
or {"sections": [{"title", "text"}]}). Output: one JSON report on stdout.
Exit: 0 PASS · 1 REVIEW (findings) · 2 SKIPPED (no input) · 3 ERROR (bad input/args).
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

PKG_ROOT = HERE.parents[2]
SHAPES_PATH = PKG_ROOT / "data" / "explain_shapes.json"
AUD_PATH = PKG_ROOT / "data" / "audiences.json"
AUD_MODULE = PKG_ROOT / "scripts" / "design" / "audience.py"

I = re.IGNORECASE


def rx(p: str, flags=I) -> re.Pattern:
    return re.compile(p, flags)


ANALOGY = rx(r"\b(?:is|are|was|works|acts|behaves|looks|feels|sounds|functions)\s+(?:a\s+(?:bit|lot)\s+|much\s+|kind\s+of\s+)?"
             r"like\b|\blike\s+(?:a|an|the|having|when|how)\b|\bthink\s+of\s+(?:it|this|that|them|\w+)\s+as\b|"
             r"\bimagine\b|\bas\s+if\b|\bas\s+though\b|\bsimilar\s+to\b|\bpicture\s+(?:a|an|this)\b|\bakin\s+to\b|"
             r"\bit'?s\s+(?:a\s+bit\s+)?like\b|\bthe\s+same\s+way\s+(?:a|an)\b")
FENCE = rx(r"\bunlike\b|\bwhere\s+(?:this|the\s+analogy|it|that|the\s+picture|the\s+comparison)\s+(?:breaks|stops|fails|"
           r"falls\s+apart|ends)|\bthe\s+analogy\s+(?:breaks|stops|ends|fails|only\s+goes)|\bthe\s+difference\b|\bexcept\b|"
           r"\bstops\s+being\s+true\b|\bbreaks\s+down\b|\bnot\s+quite\b|\bisn'?t\s+quite\b|\bdiffers?\b|\bthe\s+catch\b|"
           r"\bdoesn'?t\s+hold\b|\bfalls\s+apart\b|\bin\s+reality\b|\bbut\s+(?:a|an|the)\s+real\b|\bthe\s+limit\s+of\b")
EXAMPLE = rx(r"\bfor\s+example\b|\bfor\s+instance\b|\be\.g\.|\bsay\s+you\b|\bas\s+an\s+example\b|\bexample:|"
             r"\bhere'?s\s+an\s+example\b|\banother\s+example\b|\ba\s+second\s+example\b|\bexample\s+\d\b|"
             r"\btake\s+(?:the|a)\s+case\b|\bconsider\s+(?:the|a)\s+case\b")
ANOTHER_EXAMPLE = rx(r"\banother\s+example\b|\ba\s+second\s+example\b|\bexample\s+2\b|\bone\s+more\s+example\b")
SIMPLIFY = rx(r"\broughly\b|\bsimplif(?:y|ied|ying|ication)\b|\bloosely\b|\bglossing\s+over\b|\bmore\s+or\s+less\b|"
              r"\bnot\s+exactly\b|\bapproximately\b|\bto\s+keep\s+(?:it|this)\s+simple\b|\bin\s+simple\s+terms\b|"
              r"\bstrictly\s+speaking\b|\bthe\s+short\s+version\b|\bmostly\s+accurate\b")
TRADE_STATED = rx(r"\bprecise(?:ly)?\b|\bexact(?:ly)?\b|\bstrictly\b|\bin\s+reality\b|\bthe\s+full\s+(?:version|story|rule|"
                  r"picture)\b|\bactually\b|\btechnically\b|\bthe\s+real\s+(?:rule|version)\b")
CAVEAT = rx(r"\bnote\s+that\b|\bkeep\s+in\s+mind\b|\bbe\s+careful\b|\bcaveats?\b|\bwarning\b|\bbeware\b|\bwatch\s+out\b|"
            r"\bbe\s+aware\b|\bit\s+depends\b|\bedge\s+case\b|\bone\s+exception\b|\bin\s+rare\s+cases\b|\bheads[- ]up\b|"
            r"\bcaution\b|\bdon'?t\s+forget\b|\bbear\s+in\s+mind\b")
COST = rx(r"\bmoney\b|\bcosts?\b|\bcostly\b|\bpay\b|\bbill(?:ed|ing)?\b|\bcharg(?:e|ed|es)\b|\$|\bdata\b|\blos(?:e|es|t|ing|s)\b|"
          r"\bdelet(?:e|ed|es|ion)\b|\bcorrupt|\bsecurity\b|\bsecrets?\b|\bleak|\bcredentials?\b|\bpasswords?\b|"
          r"\bexpos(?:e|ed|es)\b|\bbreach|\birreversibl|\bcan(?:no|')?t\s+(?:be\s+)?undo|\bundone\b|\bpermanent|\boutages?\b|"
          r"\bdowntime\b|\bin\s+production\b|\bprivacy\b|\blegal\b|\bfines?\b|\bwipe|\boverwrit|\bone[- ]way\b")
SKIPPED = rx(r"\bskip(?:ped|ping|s)?\b|\bnot\s+covered\b|\bleft\s+out\b|\bleaving\s+out\b|\bset\s+aside\b|"
             r"\bthe\s+rest\s+(?:is|are|of)\b|\bout\s+of\s+scope\b|\bcan\s+wait\b|\bisn'?t\s+covered\b|\bdon'?t\s+cover\b|"
             r"\bnot\s+cover(?:ed|ing)?\b|\bfor\s+another\s+time\b|\bbeyond\s+(?:this|the\s+scope)\b|\bomit(?:ted|s)?\b|"
             r"\bboilerplate\b|\bwon'?t\s+(?:go\s+into|cover)\b|\bnot\s+going\s+into\b|\bleave\s+(?:aside|out)\b")
THROAT = rx(r"^\s*(?:(?:great|good|excellent)\s+question|there\s+are\s+(?:many|several|a\s+few|lots\s+of)\s+ways|"
            r"in\s+this\s+(?:section|video|page|article|post|guide),?\s+(?:we|you|i)(?:'ll|\s+will)|"
            r"let'?s\s+(?:explore|dive|take\s+a\s+look|begin|start|get\s+started)|before\s+we\s+(?:begin|start|dive)|"
            r"to\s+understand\s+.{0,60},\s+(?:we|you)\s+(?:first\s+)?(?:need|must|have)\s+to|historically\b|"
            r"first,?\s+(?:some|a\s+bit\s+of)\s+(?:background|history|context)|welcome\b|have\s+you\s+ever\s+wondered)")
MECH_FIRST = rx(r"^\s*(?:(?:it|this|the\s+(?:function|code|method|class|script|module|file))\s+(?:first\s+)?(?:loops|iterates|"
                r"imports|initiali[sz]es|instantiates|declares|defines|allocates)\b|(?:first|to\s+start),?\s+(?:it|the\s+code|"
                r"we)\s+(?:imports|loops|calls|reads|initiali[sz]es|declares)\b|using\s+(?:a|an|the)\b|(?:on\s+)?line\s+\d+|"
                r"(?:the|this)\s+(?:code|file)\s+(?:starts|begins)\s+(?:by|with))")
PURPOSE = rx(r"\bso\s+that\b|\bso\s+(?:it|you|the|we)\b|\b(?:its|the)\s+job\b|\bexists\s+to\b|\bis\s+for\b|\bpurpose\b|"
             r"\blets\b|\bensures?\b|\bprevents?\b|\bkeeps?\b|\bdecides?\b|\banswers?\b|\bturns?\b|\breturns?\b|"
             r"\bin\s+order\s+to\b|\bto\s+(?:make|keep|stop|let|find|decide|check|turn)\b")
VERBS = ("run|open|check|try|change|set|add|remove|replace|look|read|compare|decide|ask|pick|choose|use|start|restart|"
         "rename|call|watch|measure|test|grep|search|edit|fix|update|install|copy|paste|type|click|follow|note|keep|"
         "write|trace|find|point|put|move|turn|give|tell|approve|fund|schedule|review|go|see|make|build|swap|increase|"
         "raise|lower|reduce|drop|flip|enable|disable|toggle|pass|wrap|split|merge|mark|record|log|plan|share|send|"
         "bring|take|rerun|re-run|delete|upgrade|pin|cap|limit|retry|inspect|print|profile|benchmark|sketch|draw|push|pull|"
         "tag|commit|ship|post|publish|freeze|revert|roll|deploy|configure|connect|sign|book|email|join|list|map|label|"
         "explore|practise|practice|play|build|ask|talk|walk|imagine|picture|guess|predict|count|time|clear|save|load|fork|clone")
IMPERATIVE_START = rx(r"^\s*(?:(?:now|next|so|then|from\s+here|today|finally|first|tomorrow)[,:]?\s+)?"
                      r"(?:(?:if|when|once|before|after|to)\b[^,]{0,90},\s*)?(?:please\s+)?(?:" + VERBS + r")\b")
ACTION_ANY = rx(r"\byou\s+can\s+now\b|\bnow\s+you\s+can\b|\byou(?:'re|\s+are)\s+(?:now\s+)?ready\s+to\b|\b(?:your|the)\s+next\s+"
                r"step\b|\bthe\s+next\s+thing\s+to\b|\bthe\s+one\s+thing\s+to\s+(?:do|check|change|decide)\b|\byou(?:'ll)?\s+"
                r"(?:can|could|should|might\s+want\s+to)\s+(?!be\b|see\s+that\b)\w+|\bthis\s+means\s+we\s+should\b|"
                r"\bwe\s+should\b|\bi(?:'d)?\s+recommend\b|\bwe\s+recommend\b|\bmy\s+recommendation\b|\btry\s+(?:it|this|"
                r"running|changing|setting|adding|it\s+out)\b|\bstart\s+(?:with|by)\b|\bhere'?s\s+what\s+to\s+(?:do|check|change)\b|"
                r"\bthe\s+decision\s+(?:is|we\s+need)\b|\bask(?:ing)?\s+(?:you\s+)?(?:to|for)\s+(?:approve|fund|sign)\b")
SUMMARY_END = rx(r"^\s*(?:in\s+summary|in\s+conclusion|to\s+sum\s+up|to\s+summari[sz]e|overall|to\s+recap|in\s+short|"
                 r"all\s+in\s+all|that'?s\s+(?:how|what|why|it)|and\s+that'?s|so,?\s+in\s+the\s+end|in\s+the\s+end|"
                 r"ultimately|basically)\b")
BUSINESS_IMPACT = rx(r"\bcosts?\b|\brisk|\brevenue|\bcustomers?\b|\busers?\b|\bsav(?:e|es|ings)\b|\$\s?\d|\d+\s?%|"
                     r"\b\d+\s+(?:hours?|days?|weeks?|months?|minutes?)\b|\boutages?\b|\bdelays?\b|\bimpact\b|\bloses?\b|"
                     r"\bthis\s+means\s+we\s+should\b|\bwe\s+should\b|\brecommend|\bdecision\b|\bdecide\b|\bapprove\b|\bfund")
BUSINESS_DECISION = rx(r"\bthis\s+means\s+we\s+should\b|\bwe\s+should\b|\brecommend|\bthe\s+decision\b|\bthe\s+call\s+is\b|"
                       r"\bi'?d\s+(?:fund|approve|pick|choose|go\s+with)\b|\bask(?:ing)?\s+(?:you\s+)?(?:to|for)\b")
ERRORISH = rx(r"error|exception|traceback|fail(?:ed|ure)?|denied|refused|timed?\s?out|not\s+found|cannot|can't|undefined|"
              r"\bnull\b|fatal|panic|errno|\bE[A-Z]{3,}\b|\b[45]\d\d\b|segmentation|abort|invalid|unauthori[sz]ed|forbidden")
DEF_AFTER = rx(r"^(?:e?s)?\s*(?:\(|,\s*(?:a|an|the|which|that|meaning|i\.e\.|or|basically|short\s+for)\b|\s*[—–:]|\s+-\s|"
               r"\s+(?:is|are|means|stands\s+for|refers\s+to|is\s+short\s+for|works\s+as|acts\s+as)\b)")
DEF_BEFORE = rx(r"(?:called|known\s+as|named|the\s+term|a\s+term|so-called|what\s+we\s+call)\s+(?:an?\s+|the\s+)?[\"'“`]?\s*$")
ACRONYM = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})s?(?![A-Za-z0-9])")
ACRONYM_OK = {"OK", "TV", "US", "UK", "EU", "USA", "AM", "PM", "PDF", "FAQ", "CEO", "NOTE", "TIP", "TODO", "ID", "IDS",
              "WARNING", "CAUTION", "VS", "AKA", "ASAP", "FYI", "PS", "UTC", "GMT", "AI"}
ORDINAL_START = rx(r"^\s*(?:first(?:ly)?|second(?:ly)?|third(?:ly)?|fourth|then|next|after\s+that|afterwards|finally|lastly|"
                   r"last|step\s+\d+)\b")
CODE_SPAN = re.compile(r"`([^`]+)`")
CAMEL = re.compile(r"(?<![A-Za-z0-9_])([A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+)(?![A-Za-z0-9_])")
ABBR = ("e.g.", "i.e.", "etc.", "vs.", "Dr.", "Mr.", "Mrs.", "Ms.", "approx.", "a.k.a.", "No.", "cf.")


# ----------------------------------------------------------------------------- parsing
def split_sentences(par: str) -> list[str]:
    codes: list[str] = []

    def keep(m):
        codes.append(m.group(0))
        return f"\x00{len(codes) - 1}\x00"

    s = CODE_SPAN.sub(keep, par)
    for a in ABBR:
        s = s.replace(a, a.replace(".", "\x01"))
    s = re.sub(r"(?<=\w)\.(?=\w)", "\x01", s)
    s = re.sub(r"(?<=\d)\.(?=\d)", "\x01", s)
    parts = re.split(r"(?<=[.!?])[\"')\]”’]?\s+(?=[\"'(\[*_“‘]?[A-Z0-9\x00])", s)
    out = []
    for p in parts:
        p = p.replace("\x01", ".")
        p = re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], p)
        p = p.strip()
        if p:
            out.append(p)
    return out


def _new_section(title: str) -> dict:
    return {"title": title, "sentences": [], "code_blocks": [], "quotes": [], "headings": 1 if title else 0}


def parse_markdown(text: str) -> list[dict]:
    sections = [_new_section("")]
    para: list[str] = []
    state = {"para": 0}
    in_code = False
    code: list[str] = []

    def flush(is_list=False, ordered=False, quote=False):
        if not para:
            return
        joined = " ".join(x.strip() for x in para)
        para.clear()
        sec = sections[-1]
        for st in split_sentences(joined):
            sec["sentences"].append({"text": st, "para": state["para"], "is_list": is_list, "ordered": ordered,
                                     "quote": quote})
        state["para"] += 1

    for line in text.splitlines():
        st = line.strip()
        if st.startswith("```") or st.startswith("~~~"):
            if in_code:
                sections[-1]["code_blocks"].append({"lines": [c for c in code if c.strip()],
                                                    "after": len(sections[-1]["sentences"])})
                code = []
                in_code = False
            else:
                flush()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            sections.append(_new_section(m.group(1).strip()))
            continue
        if not st:
            flush()
            continue
        m = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", line)
        if m:
            flush()
            para.append(m.group(2))
            flush(is_list=True, ordered=m.group(1)[0].isdigit())
            continue
        m = re.match(r"^\s*>\s?(.*)$", line)
        if m:
            flush()
            sections[-1]["quotes"].append({"text": m.group(1), "after": len(sections[-1]["sentences"])})
            para.append(m.group(1))
            flush(quote=True)
            continue
        para.append(line)
    flush()
    if in_code and code:
        sections[-1]["code_blocks"].append({"lines": [c for c in code if c.strip()],
                                            "after": len(sections[-1]["sentences"])})
    return [s for s in sections if s["sentences"] or s["code_blocks"] or s["title"]]


def html_to_markdown(src: str) -> str:
    s = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", src)
    s = re.sub(r"(?is)<pre\b[^>]*>(.*?)</pre>", lambda m: "\n```\n" + re.sub(r"<[^>]+>", "", m.group(1)) + "\n```\n", s)
    s = re.sub(r"(?is)<code\b[^>]*>(.*?)</code>", lambda m: "`" + re.sub(r"<[^>]+>", "", m.group(1)) + "`", s)
    s = re.sub(r"(?is)<h[1-6]\b[^>]*>(.*?)</h[1-6]>", lambda m: "\n\n## " + re.sub(r"<[^>]+>", "", m.group(1)) + "\n\n", s)
    s = re.sub(r"(?is)<li\b[^>]*>", "\n- ", s)
    s = re.sub(r"(?is)<(?:/p|br\s*/?|/div|/section|/li|/ul|/ol|/blockquote|/tr)>", "\n\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return htmllib.unescape(s)


def parse_json(obj) -> tuple[list[dict], str]:
    def line_text(it):
        if isinstance(it, str):
            return it
        if isinstance(it, dict):
            for k in ("text", "narration", "line", "sentence", "speech"):
                if isinstance(it.get(k), str):
                    return it[k]
        return ""

    if isinstance(obj, list):
        items, mode = obj, "narration"
    elif isinstance(obj, dict) and isinstance(obj.get("sections"), list):
        out = []
        for sec in obj["sections"]:
            if not isinstance(sec, dict):
                continue
            title = str(sec.get("title") or sec.get("heading") or sec.get("id") or "")
            body = next((sec[k] for k in ("text", "markdown", "body", "content", "html") if isinstance(sec.get(k), str)), "")
            if sec.get("html") and body == sec.get("html"):
                body = html_to_markdown(body)
            parsed = parse_markdown(body)
            merged = _new_section(title)
            merged["headings"] = 1
            for p in parsed:
                merged["sentences"] += p["sentences"]
                merged["code_blocks"] += p["code_blocks"]
                merged["quotes"] += p["quotes"]
                merged["headings"] += p["headings"]
            out.append(merged)
        return out, "page"
    elif isinstance(obj, dict):
        key = next((k for k in ("lines", "narration", "beats", "scenes", "sentences") if isinstance(obj.get(k), list)), None)
        if key is None:
            raise ValueError("JSON must be a list of lines, {lines|beats|scenes|sentences: [...]} or {sections: [...]}")
        items, mode = obj[key], "narration"
    else:
        raise ValueError("unsupported JSON shape")
    sec = _new_section("")
    for i, it in enumerate(items):
        for st in split_sentences(line_text(it)):
            sec["sentences"].append({"text": st, "para": i, "is_list": False, "ordered": False, "quote": False})
    return [sec], mode


def load_input(path: Path) -> tuple[list[dict], str, str]:
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    suf = path.suffix.lower()
    if suf == ".json":
        sections, mode = parse_json(json.loads(text))
        return sections, mode, text
    if suf in (".html", ".htm"):
        return parse_markdown(html_to_markdown(text)), "page", text
    return parse_markdown(text), "text", text


# ----------------------------------------------------------------------------- helpers
def prose(s: str) -> str:
    """Sentence text with code spans blanked (code names are never jargon or analogies)."""
    return CODE_SPAN.sub(lambda m: " " * len(m.group(0)), s)


def any_rx(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, I) for p in patterns)


def word_count(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9`'’_-]+", s))


def term_rx(term: str) -> re.Pattern:
    flags = 0 if term.isupper() else I
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?:e?s)?(?![A-Za-z0-9])", flags)


def finding(rule: str, req: str, where: str, text: str, message: str, severity: str = "review") -> dict:
    return {"rule": rule, "req": req, "where": where, "text": text[:240], "message": message, "severity": severity}


def flatten(sections: list[dict]) -> list[dict]:
    out = []
    for si, sec in enumerate(sections):
        for j, s in enumerate(sec["sentences"]):
            out.append(dict(s, section=si, local=j, gi=len(out)))
    return out


def where_of(sections, s) -> str:
    title = sections[s["section"]]["title"] or f"section {s['section'] + 1}"
    return f"{title} · sentence {s['local'] + 1}"


# ----------------------------------------------------------------------------- audience
def _audience_module():
    spec = importlib.util.spec_from_file_location("c8_audience", str(AUD_MODULE))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_audience(value: str | None, subject: str | None, extra_fields: list[str], cat: dict) -> dict | None:
    if not value and not extra_fields:
        return None
    roles = {r["id"]: r for r in cat["roles"]}
    levels = {lv["id"]: lv for lv in cat["levels"]}
    res = None
    if value:
        p = Path(value)
        if p.suffix.lower() == ".json" and p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "role" in data and "level" in data and "keep_terms" in data:
                res = data
            else:
                res = _audience_module().infer(dict(data, subject=data.get("subject") or subject or ""), cat)
            res["source"] = str(p.name)
        else:
            rid, _, lvl = value.partition(":") if not value.lower().startswith("invented:") else (value, "", "")
            if value.lower().startswith("invented:"):
                res = {"role": value, "level": "informed-adult", "expert_in": [], "stand_down": False,
                       "source": "invented"}
            elif rid in roles:
                role = roles[rid]
                level = lvl if lvl in levels else role["default_level"]
                res = {"role": rid, "level": level, "expert_in": list(role.get("expert_fields", [])),
                       "stand_down": level == "specialist", "source": "role"}
            else:
                res = _audience_module().infer({"audience": value, "subject": subject or ""}, cat)
                res["source"] = "inferred"
    if res is None:
        res = {"role": "invented:field-expert", "level": "specialist", "expert_in": [], "stand_down": True,
               "source": "--expert-field"}
    res["expert_in"] = list(dict.fromkeys(list(res.get("expert_in", [])) + extra_fields))
    if extra_fields:
        res["stand_down"] = True
    base_role = roles.get(str(res["role"]).split(":")[0]) if not str(res["role"]).startswith("invented:") else None
    res["business"] = bool(base_role and base_role.get("business"))
    res["level_order"] = levels.get(res.get("level"), {"order": 3})["order"]
    return {k: res[k] for k in ("role", "level", "level_order", "expert_in", "stand_down", "business", "source")
            if k in res}


# ----------------------------------------------------------------------------- shape
def parse_parts_spec(spec: str) -> list[dict]:
    parts = []
    for chunk in [c for c in re.split(r";", spec) if c.strip()]:
        pid, _, cues = chunk.partition("=")
        pid = pid.strip()
        cue_list = [c.strip() for c in cues.split("|") if c.strip()] or [re.escape(pid.replace("_", " "))]
        parts.append({"id": pid, "label": pid, "cues": cue_list})
    return parts


def detect_shape(text: str, shapes: list[dict]) -> str | None:
    best, score = None, 0
    for sh in shapes:
        n = sum(1 for c in sh.get("detect_cues", []) if re.search(c, text, I))
        if n > score:
            best, score = sh["id"], n
    return best


def quoted_error_lines(sections, flat):
    """[(gi_position, line)] for every quoted line that looks like an error/log message."""
    out = []
    for si, sec in enumerate(sections):
        base = next((s["gi"] for s in flat if s["section"] == si), len(flat))
        for blk in sec["code_blocks"]:
            for ln in blk["lines"]:
                if ERRORISH.search(ln):
                    out.append((base + blk["after"] - 0.5, ln.strip(), "block", len(blk["lines"])))
    for s in flat:
        for m in CODE_SPAN.finditer(s["text"]):
            if ERRORISH.search(m.group(1)):
                out.append((s["gi"], m.group(1), "inline", 1))
        for m in re.finditer(r"[\"“]([^\"”]{6,200})[\"”]", s["text"]):
            if ERRORISH.search(m.group(1)):
                out.append((s["gi"], m.group(1), "quoted", 1))
        if s["quote"] and ERRORISH.search(s["text"]):
            out.append((s["gi"], s["text"], "blockquote", 1))
    return out


def ordered_steps(flat, start: int):
    """(count, first_gi, step_sentences): the first list after `start`, else ordinal sentences."""
    run, last_para = [], None
    for s in flat:
        if s["gi"] < start:
            continue
        if s["is_list"]:
            if run and s["para"] != last_para and s["para"] != last_para + 1:
                break
            if not run or s["para"] != last_para:
                run.append([s])
            else:
                run[-1].append(s)
            last_para = s["para"]
        elif run:
            break
    if len(run) >= 2:
        return len(run), run[0][0]["gi"], [x for item in run for x in item]
    ords = [s for s in flat if s["gi"] >= start and ORDINAL_START.search(prose(s["text"]))]
    return len(ords), (ords[0]["gi"] if ords else None), ords


def check_shape(shape: dict, sections, flat, code_text: str | None, findings: list) -> dict:
    parts_report = {}
    positions = []
    rules = shape.get("rules", {})
    first_gi = flat[0]["gi"] if flat else 0
    for part in shape.get("parts", []):
        kind = part.get("kind")
        pos, ok, extra = None, False, {}
        if kind == "quoted_error_line":
            q = quoted_error_lines(sections, flat)
            if q:
                pos, ok = q[0][0], True
                distinct = list(dict.fromkeys(x[1] for x in q))
                long_blocks = [x for x in q if x[2] == "block" and x[3] > int(rules.get("quote_max_lines", 1)) + 2]
                extra = {"quoted_lines": len(distinct)}
                if long_blocks or len(distinct) > int(rules.get("quote_max_lines", 1)) + 1:
                    n = long_blocks[0][3] if long_blocks else len(distinct)
                    findings.append(finding("I2.error_quote_one_line", "I2", "quoted error",
                                            distinct[0], f"quotes {n} log/error lines; quote the ONE line that "
                                            "carries the meaning and translate it"))
        elif kind == "ordered_steps":
            start = positions[-1][1] if positions else first_gi
            n, p0, steps = ordered_steps(flat, start + (1 if positions else 0))
            extra = {"count": n}
            lo, hi = int(part.get("min", 2)), int(part.get("max", 4))
            if n:
                pos = p0
            ok = lo <= n <= hi
            if n and not ok:
                findings.append(finding("I2.steps_count", "I2", "steps", steps[0]["text"] if steps else "",
                                        f"{n} parts listed; this shape wants {lo}–{hi} parts that do real work, "
                                        "in the order they run"))
            if code_text and steps and rules.get("steps_in_code_order_when_code_given", False):
                idents = []
                for s in steps:
                    names = [m.group(1) for m in CODE_SPAN.finditer(s["text"])]
                    idents += [re.sub(r"\(.*$", "", nm).strip() for nm in names[:1]]
                found_at = [(nm, code_text.find(nm)) for nm in idents if nm]
                missing = [nm for nm, at in found_at if at < 0]
                order = [at for _, at in found_at if at >= 0]
                extra.update({"code_names": idents, "missing_in_code": missing})
                if missing:
                    findings.append(finding("I2.names_not_in_code", "I2", "steps", ", ".join(missing),
                                            "named in the steps but not found in the code given (--code): read the "
                                            "code first, use its real names"))
                if order != sorted(order):
                    findings.append(finding("I2.steps_not_in_run_order", "I2", "steps", " → ".join(idents),
                                            "the steps are not in the order they appear in the code"))
        elif kind == "skipped_statement":
            hit = next((s for s in flat if SKIPPED.search(prose(s["text"]))), None)
            ok = hit is not None
            extra = {"order_exempt": True}
        elif kind == "hops":
            start = positions[-1][1] if positions else first_gi
            hops = [s for s in flat if s["gi"] >= start and any_rx(part.get("cues", []), s["text"])]
            extra = {"count": len(hops)}
            ok = len(hops) >= int(part.get("min", 3))
            pos = hops[0]["gi"] if hops else None
        elif kind == "analogy":
            hit = next((s for s in flat if ANALOGY.search(prose(s["text"]))), None)
            ok, pos = hit is not None, (hit["gi"] if hit else None)
        elif kind == "details_after_analogy":
            a = parts_report.get("analogy", {}).get("at")
            so_cues = next((p.get("cues", []) for p in shape["parts"] if p["id"] == "so_what"), [])
            det = [s for s in flat if a is not None and s["gi"] > a + 0 and not ANALOGY.search(prose(s["text"]))
                   and not any_rx(so_cues, s["text"])]
            det = [s for s in det if not (FENCE.search(prose(s["text"])) and s["gi"] <= a + 2)] or det[:0]
            extra = {"count": len(det)}
            ok = len(det) >= int(part.get("min", 1))
            pos = det[0]["gi"] if det else None
        elif kind == "example":
            n = sum(len(EXAMPLE.findall(prose(s["text"]))) for s in flat)
            extra = {"count": n}
            ok = int(part.get("min", 1)) <= n <= int(part.get("max", 1))
            hit = next((s for s in flat if EXAMPLE.search(prose(s["text"]))), None)
            pos = hit["gi"] if hit else None
            extra["order_exempt"] = True
        else:
            cues = part.get("cues", [])
            start = positions[-1][1] if positions else first_gi
            hit = next((s for s in flat if s["gi"] >= start and any_rx(cues, s["text"])), None)
            if hit is None:
                hit = next((s for s in flat if any_rx(cues, s["text"])), None)
            ok, pos = hit is not None, (hit["gi"] if hit else None)
            if ok and part.get("within_first") is not None and (pos - first_gi) >= int(part["within_first"]):
                findings.append(finding(f"I2.{part['id']}_not_first", "I2", where_of(sections, hit), hit["text"],
                                        f"'{part.get('label', part['id'])}' should come in the first "
                                        f"{part['within_first']} sentence(s)"))
            if ok and part.get("max_words") and word_count(hit["text"]) > int(part["max_words"]):
                findings.append(finding(f"I2.{part['id']}_too_long", "I2", where_of(sections, hit), hit["text"],
                                        f"'{part.get('label', part['id'])}' should be one sentence of ≤ "
                                        f"{part['max_words']} words"))
        parts_report[part["id"]] = dict({"found": ok, "at": pos}, **extra)
        if not ok:
            findings.append(finding("I2.missing_part", "I2", f"shape {shape['id']}", part.get("label", part["id"]),
                                    f"missing part '{part.get('label', part['id'])}' of the {shape['id']} shape"))
        if ok and pos is not None and not extra.get("order_exempt"):
            positions.append((part["id"], pos))
    seq = [p for p, _ in positions]
    if [x for _, x in positions] != sorted(x for _, x in positions):
        expected = [p["id"] for p in shape.get("parts", []) if p["id"] in seq]
        actual = [p for p, _ in sorted(positions, key=lambda kv: kv[1])]
        findings.append(finding("I2.order", "I2", f"shape {shape['id']}", " → ".join(actual),
                                f"parts are out of order: expected {' → '.join(expected)}"))

    # shape-specific rules
    if shape["id"] == "error":
        tc = rules.get("two_causes", {})
        mult = [s for s in flat if any_rx(tc.get("multiplicity_cues", []), s["text"])]
        cause_cues = next((p.get("cues", []) for p in shape["parts"] if p["id"] == "cause"), [])
        n_cause = sum(1 for s in flat if any_rx(cause_cues, s["text"]))
        explicit_two = any(re.search(r"two\s+(?:likely\s+|possible\s+|common\s+)?causes", s["text"], I) for s in flat)
        if explicit_two or (mult and n_cause >= 2):
            if not any(any_rx(tc.get("tell_apart_cues", []), s["text"]) for s in flat):
                findings.append(finding("I2.two_causes_no_tell_apart", "I2", "causes", (mult or flat)[0]["text"],
                                        "two likely causes are given but not how to tell them apart"))
    if shape["id"] == "architecture":
        for s in flat:
            if any_rx(rules.get("other_path_cues", []), s["text"]) and not any_rx(rules.get("deferral_cues", []), s["text"]):
                findings.append(finding("I2.more_than_one_path", "I2", where_of(sections, s), s["text"],
                                        "a second path is traced; trace ONE path end to end and leave the rest for "
                                        "when they ask"))
        seen = set()
        for s in flat:
            names = [m.group(1) for m in CODE_SPAN.finditer(s["text"])] + CAMEL.findall(prose(s["text"]))
            for nm in names:
                if nm in seen:
                    continue
                seen.add(nm)
                rest = s["text"].replace(f"`{nm}`", " ").replace(nm, " ")
                if not any_rx(rules.get("piece_role_cues", []), rest) or word_count(rest) < 3:
                    findings.append(finding("I2.piece_named_not_described", "I2", where_of(sections, s), s["text"],
                                            f"'{nm}' is named by what it is called, not by what it does"))
    if shape["id"] == "term":
        words = sum(word_count(s["text"]) for s in flat)
        heads = sum(sec["headings"] for sec in sections)
        if words > int(rules.get("max_words", 60)) or (rules.get("no_headings") and heads):
            findings.append(finding("plain.length_follows_question", "I2", "whole answer", f"{words} words, "
                                    f"{heads} heading(s)", "a one-term question deserves one or two sentences "
                                    "and no headings"))
    return {"parts": parts_report, "order": seq}


# ----------------------------------------------------------------------------- generic rules
def check_generic(sections, flat, mode: str, aud: dict | None, cat: dict, args, code_text: str | None,
                  findings: list, tradeoffs: list) -> None:
    texts = [prose(s["text"]) for s in flat]

    # lead with the answer / purpose before mechanism (C4 / I1)
    if flat:
        first = texts[0]
        if THROAT.search(first):
            findings.append(finding("plain.lead_with_answer", "I4", where_of(sections, flat[0]), flat[0]["text"],
                                    "opens with throat-clearing; the first sentence should be the answer"))
        if MECH_FIRST.search(first) and not PURPOSE.search(first):
            findings.append(finding("I1.purpose_before_mechanism", "I1", where_of(sections, flat[0]), flat[0]["text"],
                                    "opens on mechanism; say what it is FOR before how it works"))

    # business audiences: impact first, then the decision (I1)
    if aud and aud.get("business") and flat:
        if not any(BUSINESS_IMPACT.search(t) for t in texts[:2]):
            findings.append(finding("I1.business_impact_not_first", "I1", where_of(sections, flat[0]), flat[0]["text"],
                                    "a business audience needs the impact (money, risk, users, time) or the decision "
                                    "in the first two sentences"))
        if not any(BUSINESS_DECISION.search(t) for t in texts):
            findings.append(finding("I1.business_no_decision", "I1", "whole text", "",
                                    "no decision line ('this means we should …') for a business audience"))

    # I1 register fit, COUNTED (not model-graded): mean sentence length within the level's cap;
    # business audiences get no code blocks or inline code unless asked
    if aud and flat:
        lv = next((x for x in cat["levels"] if x["id"] == aud.get("level")), None)
        if lv:
            mean = sum(word_count(t) for t in texts) / len(texts)
            if mean > lv["sentence_words_max"]:
                findings.append(finding("I1.sentences_too_long_for_audience", "I1", "whole text",
                                        f"mean {mean:.1f} words/sentence",
                                        f"mean sentence length {mean:.1f} words exceeds {lv['sentence_words_max']} "
                                        f"for a {lv['label']} audience"))
        if aud.get("business"):
            n_code = sum(len(sec["code_blocks"]) for sec in sections) + sum(
                len(CODE_SPAN.findall(s["text"])) for s in flat)
            if n_code:
                findings.append(finding("I1.code_for_business_audience", "I1", "whole text", f"{n_code} code item(s)",
                                        "code blocks or inline code for a business audience: say what it means "
                                        "for the decision instead"))

    # I3 analogies must be fenced within two sentences
    for i, s in enumerate(flat):
        if ANALOGY.search(texts[i]):
            window = [texts[j] for j in range(i, min(i + 3, len(flat))) if flat[j]["section"] == s["section"]]
            if not any(FENCE.search(w) for w in window):
                findings.append(finding("I3.analogy_unfenced", "I3", where_of(sections, s), s["text"],
                                        "analogy with no limit: say where it stops being true within two sentences "
                                        "('unlike…', 'the difference is…', 'except…')"))

    # I3 one example per point (paragraph), and no "another example"
    by_para: dict = {}
    for i, s in enumerate(flat):
        n = len(EXAMPLE.findall(texts[i]))
        if n:
            by_para.setdefault((s["section"], s["para"]), []).append((n, s))
    flagged = set()
    for key, hits in by_para.items():
        if sum(n for n, _ in hits) >= 2:
            flagged.add(key)
            findings.append(finding("I3.two_examples", "I3", where_of(sections, hits[0][1]), hits[0][1]["text"],
                                    "two examples for one point: one good example beats two (the reader wonders "
                                    "which is the real case)"))
    for i, s in enumerate(flat):
        if ANOTHER_EXAMPLE.search(texts[i]) and (s["section"], s["para"]) not in flagged:
            findings.append(finding("I3.two_examples", "I3", where_of(sections, s), s["text"],
                                    "a second example for the same point"))

    # I3 the code is the example where possible
    if code_text:
        idents = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", code_text))
        for i, s in enumerate(flat):
            if EXAMPLE.search(texts[i]):
                names = [m.group(1) for m in CODE_SPAN.finditer(s["text"])]
                words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", s["text"]))
                if not any(n.split("(")[0] in code_text for n in names) and not (
                        words & {w for w in idents if "_" in w or re.search(r"[a-z][A-Z]", w)}):
                    findings.append(finding("I3.example_not_from_code", "I3", where_of(sections, s), s["text"],
                                            "the example names nothing from the code given; where code is on screen, "
                                            "the code itself is the example"))

    # I3 simplification markers are trade-offs
    for i, s in enumerate(flat):
        m = SIMPLIFY.search(texts[i])
        if m:
            nxt = texts[i + 1] if i + 1 < len(flat) and flat[i + 1]["section"] == s["section"] else ""
            stated = bool(TRADE_STATED.search(texts[i].replace(m.group(0), " ")) or TRADE_STATED.search(nxt))
            tradeoffs.append({"where": where_of(sections, s), "marker": m.group(0), "text": s["text"][:240],
                              "precise_version_stated": stated})
            findings.append(finding("I3.simplification_tradeoff", "I3", where_of(sections, s), s["text"],
                                    f"simplification ('{m.group(0)}') is a trade-off: confirm it does not teach "
                                    "something false" + ("" if stated else "; the precise version is not stated"),
                                    severity="tradeoff"))

    # I4 caveats only when skipping them costs something real
    for i, s in enumerate(flat):
        if CAVEAT.search(texts[i]):
            nxt = texts[i + 1] if i + 1 < len(flat) else ""
            if not (COST.search(texts[i]) or COST.search(nxt)):
                findings.append(finding("I4.caveat_without_cost", "I4", where_of(sections, s), s["text"],
                                        "caveat with no real cost (money, data, security, an irreversible step): "
                                        "drop it or leave the door open ('there's more if you want it')"))

    # I4 end where they can act — every section; narration: the last beat
    def ends_on_action(t: str) -> bool:
        return bool(IMPERATIVE_START.search(t) or ACTION_ANY.search(t))

    targets = []
    if mode == "narration":
        if flat:
            targets.append(flat[-1])
    else:
        for si, _sec in enumerate(sections):
            mine = [s for s in flat if s["section"] == si]
            if mine:
                targets.append(mine[-1])
    for s in targets:
        t = prose(s["text"])
        if not ends_on_action(t):
            summ = bool(SUMMARY_END.search(t))
            findings.append(finding("I4.ending_not_actionable", "I4", where_of(sections, s), s["text"],
                                    ("ends on a summary" if summ else "does not end on something the reader can do")
                                    + ": finish with what they can now do, check or decide"))

    # I4 say what was skipped
    cut = [c.strip() for c in re.split(r"[;,]", ",".join(args.cut or [])) if c.strip()]
    if cut:
        skip_sents = [s for s in flat if SKIPPED.search(prose(s["text"]))]
        if not skip_sents:
            findings.append(finding("I4.skipped_not_said", "I4", "whole text", ", ".join(cut),
                                    "content was cut but the text never says what was skipped"))
        else:
            alltext = " ".join(s["text"] for s in flat).lower()
            unnamed = [c for c in cut if c.lower() not in alltext]
            if unnamed:
                findings.append(finding("I4.skipped_item_unnamed", "I4", "whole text", ", ".join(unnamed),
                                        "says something was skipped but does not name: " + ", ".join(unnamed)))

    # I5 talking down / jargon in the same breath
    fields = {f["id"]: f for f in cat["fields"]}
    expert = set(aud.get("expert_in", [])) if aud else set()
    if aud and (aud.get("stand_down") or aud.get("level_order", 0) >= 4):
        for fid in expert:
            f = fields.get(fid)
            if not f:
                continue
            for t in f["terms"]:
                trx = term_rx(t["term"])
                for para in t.get("paraphrases", []):
                    prx = re.compile(r"(?<![A-Za-z0-9])" + re.escape(para) + r"(?![A-Za-z0-9])", I)
                    for i, s in enumerate(flat):
                        if prx.search(texts[i]):
                            if trx.search(s["text"]):
                                findings.append(finding("I5.over_explains", "I5", where_of(sections, s), s["text"],
                                                        f"defines '{t['term']}' for an audience expert in "
                                                        f"{f['label']}; they already know it"))
                            else:
                                findings.append(finding("I5.talking_down", "I5", where_of(sections, s), s["text"],
                                                        f"'{para}' replaces the field term '{t['term']}' for an "
                                                        f"audience expert in {f['label']}: keep their vocabulary"))
    non_expert = (aud is None) or (not aud.get("stand_down") and aud.get("level_order", 3) <= 3)
    jargon: list[tuple[str, list[str]]] = [(j.strip(), []) for j in re.split(r"[;,]", ",".join(args.jargon or [])) if j.strip()]
    if non_expert:
        for fid, f in fields.items():
            if fid in expert:
                continue
            jargon += [(t["term"], t.get("paraphrases", [])) for t in f["terms"]]
        keep = {t["term"].upper() for fid in expert if fid in fields for t in fields[fid]["terms"]}
        acr = []
        for i, s in enumerate(flat):
            for m in ACRONYM.finditer(texts[i]):
                a = m.group(1)
                if a not in ACRONYM_OK and a not in keep and a not in acr and not texts[i].isupper():
                    acr.append(a)
        jargon += [(a, []) for a in acr if a not in {j for j, _ in jargon}]
    seen_terms = set()
    for term, paraphrases in jargon:
        if term.lower() in seen_terms:
            continue
        seen_terms.add(term.lower())
        trx = term_rx(term)
        for i, s in enumerate(flat):
            m = trx.search(texts[i])
            if not m:
                continue
            before, after = texts[i][:m.start()], texts[i][m.end():]
            defined = bool(DEF_AFTER.search(after) or DEF_BEFORE.search(before)
                           or any(re.search(re.escape(p), texts[i], I) for p in paraphrases))
            if not defined:
                findings.append(finding("I5.jargon_undefined", "I5", where_of(sections, s), s["text"],
                                        f"'{term}' is used without a definition in the same breath "
                                        f"(e.g. '{term} (…what it is…)') for a non-expert audience"))
            break

    # length follows the question
    if args.question:
        term_shape_cues = [r"what does (?:a |an |the )?[\w`.-]+(?: [\w`.-]+){0,3} mean",
                           r"^\s*what(?:'s| is) (?:a |an |the )?[\w`.-]+(?: [\w`.-]+){0,2}\??\s*$"]
        if any(re.search(c, args.question.strip(), I) for c in term_shape_cues):
            words = sum(word_count(s["text"]) for s in flat)
            heads = sum(sec["headings"] for sec in sections)
            if words > 60 or heads:
                findings.append(finding("plain.length_follows_question", "I2", "whole answer",
                                        f"{words} words, {heads} heading(s) for: {args.question[:80]}",
                                        "a short question deserves a short answer (one or two sentences, no headings)"))


# ----------------------------------------------------------------------------- main
def run(args) -> tuple[dict, int]:
    path = Path(args.input)
    if not path.exists():
        return {"gate": "check_explain", "status": "SKIPPED", "summary": f"no input at {path}"}, EXIT_SKIPPED
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        sections, mode, _raw = load_input(path)
        shapes_cat = json.loads(SHAPES_PATH.read_text(encoding="utf-8"))
        aud_cat = json.loads(AUD_PATH.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        return {"gate": "check_explain", "status": "ERROR", "summary": f"bad input: {e}"}, EXIT_ERROR
    flat = flatten(sections)
    if not flat:
        return {"gate": "check_explain", "status": "SKIPPED", "summary": "input has no prose to judge"}, EXIT_SKIPPED
    code_text = Path(args.code).read_text(encoding="utf-8", errors="replace") if args.code else None
    findings: list = []
    tradeoffs: list = []
    notes: list = []

    shapes = {s["id"]: s for s in shapes_cat["shapes"]}
    shape_name = args.shape
    shape_source = None
    if shape_name == "auto":
        probe = args.question or " ".join(s["text"] for s in flat[:2]) + " " + " ".join(x["title"] for x in sections)
        shape_name = detect_shape(probe, shapes_cat["shapes"])
        shape_source = "auto"
        if not shape_name:
            notes.append("shape could not be detected: structure not judged")
    custom_parts = []
    if args.parts:
        custom_parts = parse_parts_spec(args.parts)
    if args.parts_file:
        custom_parts = json.loads(Path(args.parts_file).read_text(encoding="utf-8"))
    shape = None
    if custom_parts:
        shape = {"id": shape_name or "invented:custom", "parts": custom_parts, "rules": {}}
        shape_source = "custom"
    elif shape_name and shape_name in shapes:
        shape = shapes[shape_name]
        shape_source = shape_source or "catalog"
    elif shape_name:
        shape_source = "unjudged"
        notes.append(f"shape '{shape_name}' is not in the catalog and no --parts were given: structure not judged "
                     "(an invented shape is welcome; pass --parts to have its structure checked)")

    aud = resolve_audience(args.audience, args.subject, [f.strip() for f in (args.expert_field or []) if f.strip()],
                           aud_cat)
    structure = check_shape(shape, sections, flat, code_text, findings) if shape else None
    check_generic(sections, flat, mode, aud, aud_cat, args, code_text, findings, tradeoffs)

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    status = "PASS" if not findings else "REVIEW"
    by_req: dict = {}
    for f in findings:
        by_req[f["req"]] = by_req.get(f["req"], 0) + 1
    report = {
        "gate": "check_explain", "status": status,
        "summary": (f"{len(findings)} finding(s) — " + ", ".join(f"{k}: {v}" for k, v in sorted(by_req.items()))
                    if findings else "no findings (this measures structure and wording patterns, not quality)"),
        "input": str(path), "mode": mode, "input_sha256": before, "input_unchanged": before == after,
        "shape": shape["id"] if shape else shape_name, "shape_source": shape_source,
        "audience": aud, "structure": structure, "findings": findings, "tradeoffs": tradeoffs,
        "stats": {"sections": len(sections), "sentences": len(flat),
                  "words": sum(word_count(s["text"]) for s in flat)},
        "notes": notes,
        "not_judged": ["whether a simplification teaches something false (needs a reader)",
                       "whether an analogy fits its subject", "whether the steps chosen are the ones that matter",
                       "the truth of any claim (see facts_trace)"]
                      + ([] if code_text else ["run order and real names against the code (pass --code)"]),
    }
    return report, (EXIT_PASS if status == "PASS" else EXIT_REVIEW)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Advisory explanation-craft checker (reports only).")
    ap.add_argument("input", help=".md/.txt/.html or JSON (lines, beats, scenes, or sections)")
    ap.add_argument("--shape", help="error | function | architecture | concept | term | how-it-works | "
                                    "design-question | decision-brief | auto | invented:<name>")
    ap.add_argument("--parts", help='custom ordered parts: "id=cue|cue;id2=cue" (regex cues)')
    ap.add_argument("--parts-file", help="JSON list of parts [{id, label, cues, kind?, within_first?}]")
    ap.add_argument("--audience", help="role id[:level], invented:<name>, an audience/brief JSON, or free text")
    ap.add_argument("--subject", help="the subject, for audience expert detection")
    ap.add_argument("--expert-field", action="append", help="field id the audience is expert in (repeatable)")
    ap.add_argument("--cut", action="append", help="content deliberately cut (comma/semicolon list; repeatable)")
    ap.add_argument("--jargon", action="append", help="extra terms that must be defined on first use")
    ap.add_argument("--question", help="the question being answered (length follows the question)")
    ap.add_argument("--code", help="the code being explained (names and run order checked against it)")
    ap.add_argument("--out", help="also write the report JSON here (never the input)")
    a = ap.parse_args(argv)
    if a.out and Path(a.out).resolve() == Path(a.input).resolve():
        emit({"gate": "check_explain", "status": "ERROR", "summary": "--out must not be the input (report only)"})
        return EXIT_ERROR
    try:
        report, code = run(a)
    except (OSError, ValueError, KeyError, re.error) as e:
        report, code = {"gate": "check_explain", "status": "ERROR", "summary": f"{type(e).__name__}: {e}"}, EXIT_ERROR
    if a.out:
        Path(a.out).write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    emit(report)
    return code


if __name__ == "__main__":
    sys.exit(main())
