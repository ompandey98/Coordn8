#!/usr/bin/env python3
"""
Coordn8 gate — PLAIN LANGUAGE (advisory, D1): register profiles over the measurable rules of
the ASD-STE100 controlled-English standard. It REPORTS; it never rewrites (inputs are read-only,
and the report proves it by re-hashing every input after reading).

PROFILES (data/language_rules.json — a menu; an invented profile is always accepted)
  procedure    on-screen steps: <= 20 words/sentence, one instruction per sentence, imperative,
               condition before command, no semicolons, no Latin abbreviations
  description  page prose and captions: <= 25 words/sentence, <= 6 sentences/paragraph, active
               voice, <= 3 nouns in a row, no semicolons
  warning      starts WARNING or CAUTION, condition first, states the risk
  narration    REPORT-ONLY: length cap, one idea per sentence, active voice, one term per
               concept. Contractions and phrasal verbs are allowed ("you'll set up the cache"
               passes) — the standard is not written for speech. Narration findings are NOTES:
               they never change the exit code.
  invented     --profile invented:<name> --base <profile> --set key=value --reason "..."

COUNTING (controlled-english rule): a hyphenated word is 1 word; a parenthesis (bracket and all
its contents) is 1 word; a code span, a [[path:line]] chip, a URL and a number with its unit
symbol are 1 word each.

AUTO PROFILE (default): numbered list items and `steps`/`procedure` blocks -> procedure; a block
whose first word is warning/caution/danger (any case) -> warning; beatmap / narration / script
JSON -> narration; everything else (paragraphs, bullets, captions) -> description.

PART-OF-SPEECH: a local tagger is used when one is installed and its model is present
(`tagger` in the report says which); otherwise lexicon + suffix heuristics (`tagger: heuristic`).
Heuristic noun-cluster and passive detection can miss a case or flag a borderline one — every
finding is a lead the agent clears, never an automatic veto.

Usage:
    python3 scripts/gates/check_language.py steps.md                     # auto profiles
    python3 scripts/gates/check_language.py page_content.json --glossary content.json
    python3 scripts/gates/check_language.py _build/beatmap.json           # narration: notes only
    python3 scripts/gates/check_language.py captions.srt --profile description
    python3 scripts/gates/check_language.py s.md --profile invented:short --base procedure \\
        --set max_words=12 --reason "kiosk screen shows 12 words per line"
Inputs: .md/.txt, .html, .srt/.vtt, or JSON (page content.json, beatmap/narration/script,
{"blocks": [{"text", "profile"}]}, or a list of strings).
Exit: 0 PASS (notes allowed) · 1 REVIEW · 2 SKIPPED (no input) · 3 ERROR.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

PKG_ROOT = HERE.parents[2]
RULES_PATH = PKG_ROOT / "data" / "language_rules.json"
BASE_PROFILES = ("procedure", "description", "warning", "narration")


# =============================================================================== rules data
def load_rules(path: Path | None = None) -> dict:
    p = Path(path) if path else RULES_PATH
    return json.loads(p.read_text(encoding="utf-8"))


class Lex:
    """Word lists compiled once from language_rules.json."""

    def __init__(self, rules: dict):
        self.rules = rules
        self.verbs = set(rules["verbs"])
        self.verb_forms = set()
        for v in self.verbs:
            self.verb_forms.update(inflect(v))
        self.function = set(rules["function_words"])
        self.adjectives = set(rules["adjectives"])
        self.adj_suffixes = tuple(rules["adjective_suffixes"])
        self.adj_exceptions = set(rules["adjective_suffix_exceptions"])
        self.irregular = set(rules["irregular_participles"])
        self.ed_not = set(rules["ed_not_participle"])
        self.be = set(rules["be_forms"])
        self.non_imp = set(rules["non_imperative_starters"])
        self.neg_imp = [s.split() for s in rules["negated_imperative_starters"]]
        self.transitions = set(rules["transition_words"])
        self.cond_starters = set(rules["condition_starters"])
        cw = sorted(rules["condition_words"], key=len, reverse=True)
        self.cond_rx = re.compile(r"\b(?:" + "|".join(re.escape(c) for c in cw) + r")\b", re.I)
        self.latin = []
        for k, fix in rules["latin_abbreviations"].items():
            pat = re.escape(k).replace(r"\.", r"\.\s?").replace(r"\ ", r"\s+")
            self.latin.append((k, fix, re.compile(r"(?<![A-Za-z])" + pat + (r"" if k.endswith(".") else r"\b"),
                                                  re.I if k.islower() else 0)))
        self.risk = [c.lower() for c in rules["risk_cues"]]
        self.signal = list(rules["signal_words"])
        oi = rules["one_idea"]
        self.joints = [j.lower() for j in oi["joints"]]
        self.max_joints = int(oi["max_clause_joints"])
        self.filler = [f.lower() for f in rules["filler"]]


def inflect(v: str) -> set:
    out = {v, v + "s", v + "ed", v + "ing"}
    if v.endswith("e"):
        out |= {v + "d", v[:-1] + "ing"}
    if v.endswith(("s", "sh", "ch", "x", "z")):
        out.add(v + "es")
    if v.endswith("y") and len(v) > 2 and v[-2] not in "aeiou":
        out |= {v[:-1] + "ies", v[:-1] + "ied"}
    if re.match(r".*[^aeiou][aeiou][bdgmnprt]$", v) and len(v) <= 4:
        out |= {v + v[-1] + "ed", v + v[-1] + "ing"}
    return out


# =============================================================================== tagger
def load_tagger(mode: str = "auto"):
    """A local part-of-speech tagger if one is installed with its model; else None."""
    if mode == "heuristic":
        return None, "heuristic"
    try:  # pragma: no cover - depends on the host
        import nltk  # type: ignore
        nltk.pos_tag(["check"])
        return nltk.pos_tag, "nltk"
    except Exception:  # noqa: BLE001
        return None, "heuristic"


# =============================================================================== masking + counting
CODE_RX = re.compile(r"`[^`\n]+`")
CHIP_RX = re.compile(r"\[\[[^\]\n]+\]\]")
URL_RX = re.compile(r"\b(?:https?://|www\.)[^\s)\]]+")
LINK_RX = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
ABBR_PROTECT = re.compile(r"\b(?:e\.\s?g|i\.\s?e|etc|viz|cf|vs|et\s+al|N\.\s?B|ca|q\.\s?v|ibid|Dr|Mr|Mrs|Ms|No|approx|"
                          r"fig|Fig|eq|Eq|incl|min|max|sec|ver)\.", re.I)
DOT = "․"


def mask_code(text: str) -> tuple[str, list[str]]:
    """Replace code spans, chips and URLs by single CODEn tokens (each counts as one word)."""
    keep: list[str] = []

    def sub(m):
        keep.append(m.group(0))
        return f"CODETOKEN{len(keep) - 1}X"
    t = LINK_RX.sub(lambda m: m.group(1), text)
    t = CHIP_RX.sub(sub, t)
    t = CODE_RX.sub(sub, t)
    t = URL_RX.sub(sub, t)
    return t, keep


def strip_parens(text: str) -> tuple[str, int]:
    """Collapse every parenthesis (bracket + contents, nested) into one PARENTOKEN word."""
    n = 0
    prev = None
    t = text
    while prev != t:
        prev = t
        t, k = re.subn(r"\([^()]*\)", " PARENTOKEN ", t)
        n += k
    return t, n


def count_words(sentence: str) -> int:
    t, _ = mask_code(sentence)
    t, _ = strip_parens(t)
    t = re.sub(r"\s[—–]\s|[—–]", " ", t)          # dashes separate words, and are not words
    toks = [w for w in t.split() if re.search(r"[A-Za-z0-9]", w)]
    return len(toks)


def split_sentences(par: str) -> list[str]:
    t = ABBR_PROTECT.sub(lambda m: m.group(0).replace(".", DOT), par)
    # "etc." at a real sentence end: split after it when a capital follows
    t = re.sub(r"(etc" + DOT + r")\s+(?=[A-Z])", r"\1\n", t, flags=re.I)
    parts = re.split(r"(?<=[.!?])[\"'”’)\]]*\s+(?=[A-Z0-9\"'“‘(`\[])|\n+", t)
    return [p.replace(DOT, ".").strip() for p in parts if p and p.strip()]


def word_tokens(sentence: str) -> list[str]:
    t, _ = mask_code(sentence)
    t, _ = strip_parens(t)
    return re.findall(r"CODETOKEN\d+X|PARENTOKEN|[A-Za-z][A-Za-z0-9'’]*(?:-[A-Za-z0-9]+)*|\d[\d.,:]*%?", t)


# =============================================================================== input loading
class _HTMLBlocks(HTMLParser):
    BLOCK = {"p", "li", "td", "th", "figcaption", "caption", "dd", "dt", "blockquote", "aside", "summary"}
    SKIP = {"script", "style", "nav", "head", "title", "svg", "pre", "button"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.stack: list[tuple[str, dict]] = []
        self.buf: list[str] = []
        self.skip = 0
        self.in_code = 0

    def _profile(self) -> str | None:
        for tag, attrs in reversed(self.stack):
            cls = (attrs.get("class") or "") + " " + (attrs.get("data-kind") or "") + " " + (attrs.get("role") or "")
            if re.search(r"\b(?:warning|caution|danger)\b", cls, re.I):
                return "warning"
            if re.search(r"\b(?:steps?|procedure)\b", cls, re.I):
                return "procedure"
        tags = [t for t, _ in self.stack]
        if "li" in tags and "ol" in tags:
            return "procedure"
        return None

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.blocks.append({"text": text, "profile": self._profile(),
                                "where": "html:" + "/".join(t for t, _ in self.stack[-3:])})

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP or re.match(r"h[1-6]$", tag):
            self.skip += 1
            self.stack.append((tag, a))
            return
        if tag == "code":
            self.in_code += 1
            self.buf.append("`")
        if tag in self.BLOCK or tag in ("div", "section", "ol", "ul", "table", "tr", "br"):
            self._flush()
        if tag != "br":
            self.stack.append((tag, a))

    def handle_endtag(self, tag):
        if tag == "code" and self.in_code:
            self.in_code -= 1
            self.buf.append("`")
        if tag in self.BLOCK or tag in ("div", "section"):
            if not self.skip:
                self._flush()
        while self.stack:
            t, _ = self.stack.pop()
            if t in self.SKIP or re.match(r"h[1-6]$", t):
                self.skip = max(0, self.skip - 1)
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def blocks_from_markdown(text: str, where: str = "text") -> list[dict]:
    blocks: list[dict] = []
    lines = text.splitlines()
    in_fence = False
    para: list[str] = []
    n = 0

    def flush():
        nonlocal para
        if para:
            blocks.append({"text": " ".join(s.strip() for s in para), "profile": None,
                           "where": f"{where}:para{len(blocks) + 1}"})
        para = []
    for i, ln in enumerate(lines, 1):
        if ln.strip().startswith("```"):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        s = ln.strip()
        s = re.sub(r"^(?:>\s*)+", "", s)
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"(\*\*|__)(.+?)\1", r"\2", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", s).strip()
        if s.startswith("|") or re.fullmatch(r"[-=*_\s|:]+", s or "-"):
            flush()
            continue
        if not s:
            flush()
            continue
        if s.startswith("#"):
            flush()
            continue
        m = re.match(r"^(\d+)[.)]\s+(.*)$", s)
        if m:
            flush()
            n += 1
            blocks.append({"text": m.group(2), "profile": "procedure", "where": f"{where}:line{i}"})
            continue
        m = re.match(r"^[-*+]\s+(.*)$", s)
        if m:
            flush()
            blocks.append({"text": m.group(1), "profile": None, "where": f"{where}:line{i}"})
            continue
        para.append(s)
    flush()
    return blocks


def blocks_from_subtitles(text: str, where: str) -> list[dict]:
    cues = []
    for chunk in re.split(r"\n\s*\n", text.replace("\r", "")):
        ls = [l for l in chunk.splitlines() if l.strip()]
        ls = [l for l in ls if "-->" not in l and not re.fullmatch(r"\d+", l.strip())
              and not l.startswith(("WEBVTT", "NOTE", "STYLE", "REGION"))]
        if ls:
            cues.append(re.sub(r"<[^>]+>", "", " ".join(ls)))
    joined = " ".join(cues).strip()
    return [{"text": joined, "profile": "description", "where": where, "surface": "captions"}] if joined else []


def _rich(v) -> list[str]:
    """Page rich text -> paragraphs (blank-line split; '- ' lines become their own items)."""
    if v is None:
        return []
    if isinstance(v, list):
        out = []
        for x in v:
            out += _rich(x)
        return out
    if isinstance(v, dict):
        if "html" in v:
            p = _HTMLBlocks()
            p.feed(str(v["html"]))
            p.close()
            return [b["text"] for b in p.blocks]
        return []
    out = []
    for par in re.split(r"\n\s*\n", str(v)):
        items = [l for l in par.splitlines()]
        bullets = [l for l in items if l.strip().startswith("- ")]
        if bullets and len(bullets) == len([l for l in items if l.strip()]):
            out += [l.strip()[2:] for l in bullets]
        elif par.strip():
            out.append(" ".join(l.strip() for l in items))
    return out


def blocks_from_page(doc: dict) -> list[dict]:
    blocks: list[dict] = []

    def add(text, profile, where):
        for i, par in enumerate(_rich(text)):
            blocks.append({"text": par, "profile": profile, "where": where + (f"#{i + 1}" if i else ""),
                           "surface": "page"})
    for key in ("conclusion", "lede"):
        add(doc.get(key), None, key)
    for si, sec in enumerate(doc.get("sections") or []):
        sid = sec.get("id") or f"section{si + 1}"
        for key in ("summary", "what", "why", "how"):
            add(sec.get(key), sec.get("profile"), f"{sid}.{key}")
        for ci, comp in enumerate(sec.get("components") or []):
            cid = comp.get("id") or f"{sid}.c{ci + 1}"
            ctype = str(comp.get("type") or "")
            prof = comp.get("profile")
            kind = str(comp.get("kind") or "")
            if ctype in ("callout", "note", "warning") and (kind in ("warning", "caution") or ctype == "warning"):
                title = str(comp.get("title") or "")
                body = " ".join(_rich(comp.get("body")))
                txt = (title.rstrip(":") + ": " + body) if title and re.match(r"(?i)\s*(warning|caution)\b", title) \
                    else body
                blocks.append({"text": txt, "profile": prof or "warning", "where": cid, "surface": "page"})
                continue
            if ctype in ("steps", "procedure") or prof == "procedure":
                for k, st in enumerate(comp.get("steps") or []):
                    t = st if isinstance(st, str) else (st.get("text") or st.get("body") or "")
                    blocks.append({"text": t, "profile": prof or "procedure", "where": f"{cid}.step{k + 1}",
                                   "surface": "page"})
                continue
            add(comp.get("caption"), prof, f"{cid}.caption")
            add(comp.get("body"), prof, f"{cid}.body")
            if ctype in ("stepper", "example"):
                for k, st in enumerate(comp.get("steps") or []):
                    if isinstance(st, dict):
                        add(st.get("body"), prof, f"{cid}.step{k + 1}")
    return blocks


def _line_text(it) -> str:
    if isinstance(it, str):
        return it
    if isinstance(it, dict):
        return str(it.get("text") or it.get("narration") or it.get("line") or "")
    return ""


def blocks_from_json(obj, where: str) -> list[dict]:
    if isinstance(obj, list):
        return [{"text": _line_text(x), "profile": (x.get("profile") if isinstance(x, dict) else None) or "narration",
                 "where": f"{where}[{i}]", "surface": "voice"} for i, x in enumerate(obj) if _line_text(x).strip()]
    if not isinstance(obj, dict):
        return []
    if isinstance(obj.get("blocks"), list):
        out = []
        for i, b in enumerate(obj["blocks"]):
            if isinstance(b, dict) and str(b.get("text") or "").strip():
                out.append({"text": str(b["text"]), "profile": b.get("profile") or b.get("kind"),
                            "where": b.get("where") or f"{where}.blocks[{i}]",
                            "surface": b.get("surface") or "text"})
        return out
    if isinstance(obj.get("sections"), list) and ("title" in obj or "conclusion" in obj):
        return blocks_from_page(obj)
    out = []
    for key in ("sentences", "lines", "beats"):
        if isinstance(obj.get(key), list):
            out += [{"text": _line_text(x), "profile": "narration", "where": f"{key}[{i}]", "surface": "voice"}
                    for i, x in enumerate(obj[key]) if _line_text(x).strip()]
    scenes = obj.get("scenes")
    if isinstance(scenes, dict):
        scenes = [dict(v, id=k) for k, v in scenes.items() if isinstance(v, dict)]
    if isinstance(scenes, list):
        for sc in scenes:
            if not isinstance(sc, dict):
                continue
            sid = sc.get("id") or "scene"
            lines = sc.get("lines") or sc.get("sentences") or []
            for i, x in enumerate(lines):
                if _line_text(x).strip():
                    out.append({"text": _line_text(x), "profile": "narration", "where": f"{sid}[{i}]",
                                "surface": "voice"})
            if not lines and str(sc.get("narration") or "").strip():
                out.append({"text": str(sc["narration"]), "profile": "narration", "where": str(sid),
                            "surface": "voice"})
    return out


def load_blocks(path: Path) -> tuple[list[dict], dict]:
    """Return (blocks, extra) where extra may carry 'profiles' and 'glossary' found in the file."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", "replace")
    suf = path.suffix.lower()
    extra: dict = {}
    if suf == ".json":
        obj = json.loads(text)
        if isinstance(obj, dict):
            if isinstance(obj.get("profiles"), dict):
                extra["profiles"] = obj["profiles"]
            if isinstance(obj.get("glossary"), list):
                extra["glossary"] = obj["glossary"]
            if isinstance(obj.get("claims"), list):
                extra["claims"] = obj["claims"]
        return blocks_from_json(obj, path.name), extra
    if suf in (".srt", ".vtt"):
        return blocks_from_subtitles(text, path.name), extra
    if suf in (".html", ".htm"):
        p = _HTMLBlocks()
        p.feed(text)
        p.close()
        for b in p.blocks:
            b["surface"] = "page"
        return p.blocks, extra
    bl = blocks_from_markdown(text, path.name)
    for b in bl:
        b.setdefault("surface", "text")
    return bl, extra


def auto_profile(block: dict) -> str:
    p = block.get("profile")
    if p:
        return str(p)
    if re.match(r"(?i)\s*(?:warning|caution|danger)\b\s*[:!—–-]", block["text"]):
        return "warning"
    return "description"


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# =============================================================================== rule helpers
def _lw(tok: str) -> str:
    return tok.lower().replace("’", "'")


def is_participle(tok: str, lex: Lex) -> bool:
    w = _lw(tok)
    if w in lex.irregular:
        return True
    return bool(re.fullmatch(r"[a-z]{2,}ed", w)) and w not in lex.ed_not and len(w) >= 4


def find_passive(tokens: list[str], lex: Lex, tagged=None) -> str | None:
    if tagged:  # pragma: no cover - tagger path
        for i, (w, t) in enumerate(tagged):
            if _lw(w) in lex.be:
                for j in range(i + 1, min(i + 4, len(tagged))):
                    if tagged[j][1] == "VBN":
                        return " ".join(x for x, _ in tagged[i:j + 1])
                    if tagged[j][1] not in ("RB", "RBR", "RBS"):
                        break
        return None
    for i, tok in enumerate(tokens):
        if _lw(tok) not in lex.be:
            continue
        j = i + 1
        skipped = 0
        while j < len(tokens) and skipped < 2 and (_lw(tokens[j]).endswith("ly") or
                                                   _lw(tokens[j]) in ("not", "also", "then", "always", "never",
                                                                      "often", "usually", "being", "been", "first",
                                                                      "still", "already", "only", "automatically")):
            if _lw(tokens[j]) in ("being", "been"):
                pass
            j += 1
            skipped += 1
        if j < len(tokens) and is_participle(tokens[j], lex):
            # "been" followed by a participle is the passive; "been" alone is not
            return " ".join(tokens[i:j + 1])
    return None


def nounish(tok: str, prev: str | None, lex: Lex, prev_noun: bool = False) -> bool:
    if tok.startswith("CODETOKEN"):
        return True
    if tok == "PARENTOKEN" or re.match(r"\d", tok):
        return False
    w = _lw(tok)
    if "'" in w or w in lex.function or w in lex.adjectives:
        return False
    if w.endswith("ly") and len(w) > 4:
        return False
    if w.endswith(lex.adj_suffixes) and w not in lex.adj_exceptions:
        return False
    if re.fullmatch(r"[a-z]+ed", w) and w not in lex.ed_not:
        return False
    if w.endswith("ing"):
        return prev is not None and (prev in ("the", "a", "an", "this", "that", "its", "their", "our", "your")
                                     or prev_noun or nounish_simple(prev, lex))
    if w in lex.verb_forms:
        # A verb-shaped word is a noun only inside a noun phrase: after a determiner/noun and not
        # in a third-person "-s" slot after a noun ("the service requests images" -> verb).
        if prev is None:
            return False
        if prev in ("the", "a", "an", "this", "that", "these", "those", "its", "their", "our", "your", "each",
                    "every", "no", "any", "some"):
            return True
        if prev.endswith("s") and not prev.endswith("ss") and w in lex.verbs:
            return False  # plural subject + base verb: "channels use", "requests wait"
        if (prev_noun or nounish_simple(prev, lex)) and not (w.endswith("s") and w[:-1] in lex.verbs or
                                              w.endswith("es") and w[:-2] in lex.verbs):
            return True
        return False
    return True


def nounish_simple(w: str, lex: Lex) -> bool:
    return bool(w) and w not in lex.function and w not in lex.adjectives and not w.endswith("ly") \
        and w not in lex.verbs and re.fullmatch(r"[a-z][a-z0-9-]*", w) is not None


def noun_clusters(tokens: list[str], lex: Lex, tagged=None, limit: int = 3) -> list[str]:
    runs: list[list[str]] = []
    cur: list[str] = []
    if tagged:  # pragma: no cover - tagger path
        for w, t in tagged:
            if t.startswith("NN"):
                cur.append(w)
            else:
                if len(cur) > limit:
                    runs.append(cur)
                cur = []
        if len(cur) > limit:
            runs.append(cur)
        return [" ".join(r) for r in runs]
    prev = None
    prev_ok = False
    for i, tok in enumerate(tokens):
        ok = nounish(tok, prev, lex, prev_ok) if i else (nounish(tok, None, lex) and _lw(tok) not in lex.verbs)
        prev_ok = ok
        if ok:
            cur.append(tok)
        else:
            if len(cur) > limit:
                runs.append(cur)
            cur = []
        prev = _lw(tok)
    if len(cur) > limit:
        runs.append(cur)
    return [" ".join(r) for r in runs]


CHUNK_SPLIT = re.compile(r"[,;:!?|→←⇒—–\"“”/+=<>*]|\s-\s|\.\s")


def clusters_in(sentence: str, lex: Lex, tagged=None) -> list[str]:
    """Noun clusters never cross punctuation; a run of Capitalised words (a product name) is one noun."""
    if tagged:  # pragma: no cover - tagger path
        return noun_clusters([], lex, tagged)
    masked, _ = mask_code(sentence)
    masked, _ = strip_parens(masked)
    out: list[str] = []
    for chunk in CHUNK_SPLIT.split(masked):
        toks = word_tokens(chunk)
        merged: list[str] = []
        for t in toks:
            if merged and t[:1].isupper() and merged[-1][:1].isupper() and not t.startswith(("CODETOKEN", "PARENTOKEN")) \
                    and not merged[-1].startswith(("CODETOKEN", "PARENTOKEN")):
                merged[-1] = merged[-1] + "_" + t
            else:
                merged.append(t)
        out += noun_clusters(merged, lex)
    return out


def strip_lead(words: list[str], lex: Lex) -> list[str]:
    while words and _lw(words[0]).rstrip(",:") in lex.transitions:
        words = words[1:]
    return words


def main_clause(sentence: str, lex: Lex) -> tuple[str, bool]:
    """(clause that should carry the command, whether a leading condition was present)."""
    s = sentence.strip().lstrip("\"'“‘")
    words = strip_lead(s.split(), lex)
    s = " ".join(words)
    first = _lw(words[0]).rstrip(",") if words else ""
    two = " ".join(_lw(w) for w in words[:3])
    if first in lex.cond_starters or two.startswith(("as long as", "in case", "provided that")):
        if first in ("to", "for", "in", "on", "with", "during", "while") and "," not in s:
            return s, False
        if "," in s:
            return s.split(",", 1)[1].strip(), True
        return "", True
    return s, False


def is_imperative(clause: str, lex: Lex) -> tuple[bool, str]:
    masked, _ = mask_code(clause)
    if re.match(r"\W*CODETOKEN\d+X", masked):
        return True, "code"  # a step that opens on a code name is not judged (it may be a command line)
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", masked)]
    words = strip_lead(words, lex)
    if not words:
        return True, ""
    lw = [_lw(w) for w in words]
    for starter in lex.neg_imp:
        if lw[:len(starter)] == starter:
            return True, " ".join(starter)
    w0 = lw[0]
    if w0 in lex.verbs:
        return True, w0
    if w0 in lex.non_imp or w0.endswith(("ing", "ed")) or (w0.endswith("ly") and len(w0) > 4):
        return False, w0
    return True, w0  # unknown word: assume a verb we do not list (never fail for lexicon gaps)


def count_instructions(clause: str, lex: Lex) -> int:
    segs = re.split(r",?\s+(?:and\s+then|and|then|but|or)\s+|;\s*|,\s+(?=then\b)", clause, flags=re.I)
    n = 0
    for i, sg in enumerate(segs):
        ws = strip_lead(re.findall(r"[A-Za-z][A-Za-z'’-]*", sg), lex)
        if not ws:
            continue
        w0 = _lw(ws[0])
        if w0 in lex.verbs and w0 not in lex.function:
            # "the read and write paths": a verb word followed by a noun-phrase ending is not a new step
            if i > 0 and len(ws) == 1:
                continue
            n += 1
        elif i == 0 and any(_lw(" ".join(ws[:len(st)])) == " ".join(st) for st in lex.neg_imp):
            n += 1
    return n


def has_phrase(text_l: str, phrase: str) -> bool:
    if phrase in ("~",):
        return phrase in text_l
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text_l) is not None


# =============================================================================== the checker
def resolve_profiles(rules: dict, extra_profiles: dict | None, cli_invented: dict | None) -> tuple[dict, list]:
    profiles = {k: dict(v) for k, v in rules["profiles"].items()}
    invented_log = []

    def add_invented(name: str, spec: dict):
        base = spec.get("base") or "description"
        if base not in profiles:
            base = "description"
        p = dict(profiles[base])
        for k, v in spec.items():
            if k not in ("base", "reason"):
                p[k] = v
        p["invented"] = True
        p["base"] = base
        p["reason"] = spec.get("reason") or ""
        profiles[name] = p
        invented_log.append({"profile": name, "base": base, "reason": p["reason"] or "(no reason given)",
                             "overrides": {k: v for k, v in spec.items() if k not in ("base", "reason")}})
    for name, spec in (extra_profiles or {}).items():
        if name not in BASE_PROFILES and isinstance(spec, dict):
            add_invented(name, spec)
    if cli_invented:
        add_invented(cli_invented["name"], cli_invented["spec"])
    return profiles, invented_log


def check_blocks(blocks: list[dict], rules: dict | None = None, *, extra_profiles: dict | None = None,
                 cli_invented: dict | None = None, forced_profile: str | None = None,
                 glossary: list | None = None, aliases: dict | None = None,
                 dictionary: set | None = None, tagger_mode: str = "auto") -> dict:
    rules = rules or load_rules()
    lex = Lex(rules)
    profiles, invented_log = resolve_profiles(rules, extra_profiles, cli_invented)
    tag_fn, tagger_name = load_tagger(tagger_mode)
    findings: list[dict] = []
    profiles_used: dict[str, int] = {}
    sentences_checked = 0
    narration_texts: list[str] = []
    unknown_profiles: list[str] = []

    def add(rule, prof, sev, where, sentence, message, **kw):
        f = {"rule": rule, "profile": prof, "severity": sev, "where": where,
             "sentence": sentence[:240], "message": message}
        f.update(kw)
        findings.append(f)

    for b in blocks:
        prof_name = forced_profile or auto_profile(b)
        if prof_name not in profiles:
            unknown_profiles.append(prof_name)
            prof_name = "description"
        prof = profiles[prof_name]
        profiles_used[prof_name] = profiles_used.get(prof_name, 0) + 1
        base_sev = prof.get("severity", "review")
        active = set(prof.get("rules") or [])
        notes = set(prof.get("note_rules") or [])

        def sev_of(rule):
            if rule in active:
                return base_sev
            if rule in notes:
                return "note"
            return None
        text = htmllib.unescape(b["text"]).strip()
        where = b.get("where") or "text"
        if prof_name == "narration" or prof.get("base") == "narration":
            narration_texts.append(text)

        body = text
        # ---- warning: signal word + risk (block level)
        if sev_of("signal_word"):
            m = re.match(r"\s*(WARNING|CAUTION)\b\s*[:!—–-]?\s*", body)
            if not m:
                mm = re.match(r"\s*(warning|caution|danger|important|note|careful|be careful)\b", body, re.I)
                if mm:
                    add("signal_word", prof_name, sev_of("signal_word"), where, body,
                        f"start a safety instruction with the uppercase signal word WARNING (risk to people or data) "
                        f"or CAUTION (risk to equipment or work), not {mm.group(1)!r}")
                else:
                    add("signal_word", prof_name, sev_of("signal_word"), where, body,
                        "a warning starts with WARNING or CAUTION")
            if m:
                body = body[m.end():]
            if sev_of("states_risk"):
                low = body.lower()
                if not any(has_phrase(low, c) for c in lex.risk):
                    add("states_risk", prof_name, sev_of("states_risk"), where, text,
                        "the warning gives a command but not the risk: say what happens if it is ignored "
                        "(for example 'the data on the drive can become corrupt')")
        sents = split_sentences(body)
        if sev_of("paragraph_length"):
            cap = prof.get("max_sentences_per_paragraph")
            if cap and len(sents) > int(cap):
                add("paragraph_length", prof_name, sev_of("paragraph_length"), where, text,
                    f"{len(sents)} sentences in one paragraph (limit {cap}); split it", sentences=len(sents))
        for s in sents:
            sentences_checked += 1
            toks = word_tokens(s)
            tagged = None
            if tag_fn:  # pragma: no cover - tagger path
                try:
                    tagged = tag_fn([t for t in toks])
                except Exception:  # noqa: BLE001
                    tagged = None
            n = count_words(s)
            if sev_of("length") and prof.get("max_words") and n > int(prof["max_words"]):
                add("length", prof_name, sev_of("length"), where, s,
                    f"{n} words (limit {prof['max_words']} for {prof_name}); split it or cut words", words=n)
            if sev_of("no_semicolon"):
                masked, _ = mask_code(s)
                if ";" in masked:
                    add("no_semicolon", prof_name, sev_of("no_semicolon"), where, s,
                        "semicolon: make two sentences")
            if sev_of("no_latin_abbreviation"):
                masked, _ = mask_code(s)
                for k, fix, rxp in lex.latin:
                    if rxp.search(masked):
                        add("no_latin_abbreviation", prof_name, sev_of("no_latin_abbreviation"), where, s,
                            f"Latin abbreviation {k!r}: write {fix!r}", abbreviation=k)
            if sev_of("active_voice"):
                pv = find_passive(toks, lex, tagged)
                if pv:
                    add("active_voice", prof_name, sev_of("active_voice"), where, s,
                        f"passive voice ({pv!r}): say who does what", passive=pv)
            if sev_of("noun_cluster"):
                for run in clusters_in(s, lex, tagged):
                    add("noun_cluster", prof_name, sev_of("noun_cluster"), where, s,
                        f"{len(run.split())} nouns in a row ({run!r}, limit 3): use a preposition or split it",
                        cluster=run)
            if sev_of("one_idea"):
                low = " " + s.lower() + " "
                joints = sum(low.count(j) for j in lex.joints) + low.count(";")
                if joints > lex.max_joints:
                    add("one_idea", prof_name, sev_of("one_idea"), where, s,
                        f"{joints + 1} clauses in one sentence: one idea per sentence", clauses=joints + 1)
            if sev_of("filler"):
                low = s.lower()
                hits = [f for f in lex.filler if has_phrase(low, f)]
                if hits:
                    add("filler", prof_name, sev_of("filler"), where, s,
                        f"filler or marketing word(s) {hits}: cut them or say the concrete thing", words_found=hits)
            clause, had_cond = main_clause(s, lex)
            if sev_of("imperative") and clause:
                ok, w0 = is_imperative(clause, lex)
                if not ok:
                    add("imperative", prof_name, sev_of("imperative"), where, s,
                        f"a step starts with a command verb (found {w0!r}): 'Open the file', not 'You open the file'")
            if sev_of("one_instruction") and clause:
                k = count_instructions(clause, lex)
                if k > 1:
                    add("one_instruction", prof_name, sev_of("one_instruction"), where, s,
                        f"{k} instructions in one sentence: one step per sentence", instructions=k)
            if sev_of("condition_first") and clause and not had_cond:
                ok, w0 = is_imperative(clause, lex)
                ok = ok and w0 != "code"
                words = clause.split()
                rest = " ".join(words[1:])
                m = lex.cond_rx.search(rest)
                if ok and m:
                    add("condition_first", prof_name, sev_of("condition_first"), where, s,
                        f"the condition ({m.group(0)!r} …) comes after the command: put the condition first "
                        f"('If …, do …')", condition=m.group(0))
            if dictionary is not None and prof_name != "narration":
                gl = {str(g.get("term", "")).lower() for g in (glossary or []) if isinstance(g, dict)}
                for i, t in enumerate(toks):
                    if t.startswith(("CODETOKEN", "PARENTOKEN")) or re.match(r"\d", t):
                        continue
                    if i and t[:1].isupper():
                        continue
                    w = _lw(t)
                    forms = {w, w[:-1] if w.endswith("s") else w, w[:-2] if w.endswith("es") else w,
                             w[:-2] if w.endswith("ed") else w, w[:-1] if w.endswith("d") else w,
                             w[:-3] if w.endswith("ing") else w, (w[:-3] + "e") if w.endswith("ing") else w}
                    if not (forms & dictionary) and w not in gl:
                        add("dictionary", prof_name, "note", where, s,
                            f"{t!r} is not in the supplied approved-word list", word=t)

    # ---- one term per concept (narration): reported as notes through the terms checker
    if narration_texts:
        try:
            sys.path.insert(0, str(HERE.parent))
            import check_terms  # noqa: WPS433
            rep = check_terms.analyse({"voice": "\n".join(narration_texts)}, glossary or [], aliases or {},
                                      code_names=[], rules=rules, use_builtin=True)
            for f in rep["findings"] + rep["notes"]:
                add("one_term", "narration", "note", "voice", f.get("example", ""), f["message"],
                    concept=f.get("concept"))
        except Exception as e:  # noqa: BLE001
            add("one_term", "narration", "note", "voice", "", f"terms check could not run: {e}")

    review = [f for f in findings if f["severity"] == "review"]
    notes_ = [f for f in findings if f["severity"] != "review"]
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    return {"findings": review, "notes": notes_, "counts": counts, "profiles_used": profiles_used,
            "sentences_checked": sentences_checked, "tagger": tagger_name, "invented_profiles": invented_log,
            "unknown_profiles": sorted(set(unknown_profiles))}


# =============================================================================== CLI
def parse_set(items: list[str]) -> dict:
    out = {}
    for it in items or []:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        try:
            out[k.strip()] = json.loads(v)
        except Exception:  # noqa: BLE001
            out[k.strip()] = v
    return out


def load_glossary(path: str | None) -> list:
    if not path:
        return []
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return obj
    for k in ("glossary", "terms", "entries"):
        if isinstance(obj.get(k), list):
            return obj[k]
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 plain-language checker (report only).")
    ap.add_argument("inputs", nargs="*")
    ap.add_argument("--profile", default="auto",
                    help="auto | procedure | description | warning | narration | invented:<name>")
    ap.add_argument("--base", default=None, help="base profile for an invented profile")
    ap.add_argument("--set", action="append", default=[], help="override for an invented profile, key=value")
    ap.add_argument("--reason", default=None, help="why the invented profile fits")
    ap.add_argument("--rules", default=None, help="alternative language_rules.json")
    ap.add_argument("--glossary", default=None, help="glossary JSON (one term per concept in narration)")
    ap.add_argument("--aliases", default=None, help="agent-written alias list (see check_terms.py)")
    ap.add_argument("--dictionary", default=None,
                    help="local approved-word list (one word per line); words outside it are noted")
    ap.add_argument("--tagger", default="auto", choices=["auto", "heuristic"])
    args = ap.parse_args(argv)

    if not args.inputs:
        emit({"gate": "language", "status": "SKIPPED", "summary": "no input supplied"})
        return EXIT_SKIPPED
    try:
        rules = load_rules(Path(args.rules) if args.rules else None)
    except Exception as e:  # noqa: BLE001
        emit({"gate": "language", "status": "ERROR", "summary": f"rules unreadable: {e}"})
        return EXIT_ERROR

    forced = None if args.profile == "auto" else args.profile
    cli_inv = None
    if forced and forced not in BASE_PROFILES:
        if not forced.startswith("invented:"):
            emit({"gate": "language", "status": "ERROR",
                  "summary": f"unknown profile {forced!r}: use one of {BASE_PROFILES} or invented:<name>"})
            return EXIT_ERROR
        spec = parse_set(args.set)
        spec["base"] = args.base or "description"
        spec["reason"] = args.reason or ""
        cli_inv = {"name": forced, "spec": spec}

    hashes_before, blocks, extra_profiles, missing = {}, [], {}, []
    glossary = load_glossary(args.glossary) if args.glossary else []
    try:
        for inp in args.inputs:
            p = Path(inp)
            if not p.exists():
                missing.append(inp)
                continue
            hashes_before[inp] = sha(p)
            bl, extra = load_blocks(p)
            blocks += bl
            extra_profiles.update(extra.get("profiles") or {})
            if not glossary and extra.get("glossary"):
                glossary = extra["glossary"]
    except Exception as e:  # noqa: BLE001
        emit({"gate": "language", "status": "ERROR", "summary": f"could not read input: {type(e).__name__}: {e}"})
        return EXIT_ERROR
    if not blocks:
        emit({"gate": "language", "status": "SKIPPED", "missing": missing,
              "summary": "no text found in the input(s)"})
        return EXIT_SKIPPED
    aliases = {}
    if args.aliases:
        sys.path.insert(0, str(HERE.parent))
        import check_terms  # noqa: WPS433
        aliases = check_terms.load_aliases(Path(args.aliases))
    dictionary = None
    if args.dictionary:
        dp = Path(args.dictionary)
        if dp.exists():
            dictionary = {w.strip().lower() for w in dp.read_text(encoding="utf-8").replace(",", "\n").splitlines()
                          if w.strip() and not w.startswith("#")}
    rep = check_blocks(blocks, rules, extra_profiles=extra_profiles, cli_invented=cli_inv, forced_profile=forced,
                       glossary=glossary, aliases=aliases, dictionary=dictionary, tagger_mode=args.tagger)
    unchanged = all(sha(Path(k)) == v for k, v in hashes_before.items())
    not_checked = []
    if dictionary is None:
        not_checked.append("approved-word dictionary (no local word list supplied with --dictionary)")
    if rep["tagger"] == "heuristic":
        not_checked.append("part-of-speech by a trained tagger (none installed; lexicon heuristics used)")
    n_rev = len(rep["findings"])
    status = "REVIEW" if n_rev else "PASS"
    only_narr = set(rep["profiles_used"]) <= {"narration"}
    summary = (f"{n_rev} plain-language finding(s) to review over {rep['sentences_checked']} sentence(s)"
               if n_rev else f"no plain-language findings to review over {rep['sentences_checked']} sentence(s)")
    if rep["notes"]:
        summary += f"; {len(rep['notes'])} note(s)"
    if only_narr:
        summary += " — narration is REPORT-ONLY (notes never fail, the voice is never rewritten)"
    emit({"gate": "language", "status": status, "tagger": rep["tagger"],
          "narration_policy": "report-only: findings are notes, the text is never rewritten",
          "profiles_used": rep["profiles_used"], "sentences_checked": rep["sentences_checked"],
          "counts": rep["counts"], "findings": rep["findings"][:200], "notes": rep["notes"][:200],
          "invented_profiles": rep["invented_profiles"], "unknown_profiles": rep["unknown_profiles"],
          "inputs_sha256": hashes_before, "inputs_unchanged": unchanged, "missing": missing,
          "not_checked": not_checked, "summary": summary})
    return EXIT_REVIEW if n_rev else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
