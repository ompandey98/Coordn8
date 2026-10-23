#!/usr/bin/env python3
"""
Coordn8 gate — ONE TERM PER CONCEPT (advisory, D2) across the page, the captions and the voice.

Driven by two inputs the build already has or the agent writes:
  * the project GLOSSARY — entries {term, aliases?, synonyms?, code?}. `aliases` are ACCEPTED
    forms of the same word (plurals, inflections: "miss" / "misses"), exactly as the page
    template reads them. `code` is the identifier as written in the code.
  * an ALIAS LIST the agent writes while reading the code and drafts: other names that are
    being used for the same concept. Shapes accepted:
        {"worker": ["processor", "executor"]}
        {"aliases": {"worker": ["processor"]}}
        [{"term": "worker", "synonyms": ["processor"]}]
    Every listed synonym that appears in a deliverable is flagged: pick the glossary term.

WHAT IT FLAGS
  synonym       a listed synonym used anywhere ("processor" when the glossary says "worker"),
                with the surfaces that use each form — page / captions / voice drift is named
  code_recased  a code name written in different case or with different separators from the
                code (`RateLimiter` written "Ratelimiter", "rateLimiter", "rate_limiter")
  code_translated  a code name split into Title-Case words ("Rate Limiter") — code names stay
                exactly as in code; plain lower-case prose ("the rate limiter") is a NOTE only
  builtin_set   (note) two words from a built-in synonym set (data/language_rules.json) both
                used, when no glossary entry settles them
Code names come from glossary `code`, --code-names (one per line), and every back-ticked
identifier with inner structure (CamelCase, snake_case, dotted) found in any surface.
A definition that names a synonym on purpose ("also called a processor") is not flagged.

Usage:
    python3 scripts/gates/check_terms.py --glossary content.json --aliases _research/aliases.json \\
        --page content.json --captions out.srt --voice _build/beatmap.json [--code-names names.txt]
    python3 scripts/gates/check_terms.py --glossary g.json --text notes=draft.md
Exit: 0 PASS (notes allowed) · 1 REVIEW · 2 SKIPPED (no text) · 3 ERROR. Inputs are never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

DEFINING = re.compile(r"\b(?:also\s+(?:called|known\s+as)|sometimes\s+called|a\.?k\.?a\.?|other\s+names?\s+(?:is|are|for)|"
                      r"not\s+to\s+be\s+confused\s+with|(?:formerly|previously)\s+(?:called|named))\s*(?:(?:a|an|the)\s+)?[\"'“`]?\s*$", re.I)
CODE_SPAN = re.compile(r"`([^`\n]+)`")


def word_rx(word: str) -> re.Pattern:
    w = re.escape(word.strip()).replace(r"\ ", r"\s+")
    return re.compile(r"(?<![A-Za-z0-9_])" + w + r"(?:e?s)?(?![A-Za-z0-9_])", re.I)


def split_ident(name: str) -> list[str]:
    parts = re.split(r"[._\-/:\s]+", name)
    out = []
    for p in parts:
        out += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", p)
    return [x for x in out if x]


def structured_ident(name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?", name):
        return False
    core = name.rstrip("()")
    return bool(re.search(r"[a-z][A-Z]|[A-Z][a-z]+[A-Z]|_|\.", core)) and len(split_ident(core)) >= 2


def load_aliases(path: Path) -> dict:
    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, list[str]] = {}
    if isinstance(obj, dict) and isinstance(obj.get("aliases"), (dict, list)):
        obj = obj["aliases"]
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list):
                out[str(k)] = [str(x) for x in v]
            elif isinstance(v, str):
                out[str(k)] = [v]
    elif isinstance(obj, list):
        for e in obj:
            if isinstance(e, dict) and e.get("term"):
                syn = e.get("synonyms") or e.get("aliases") or e.get("other_names") or []
                out[str(e["term"])] = [str(x) for x in syn]
    return out


def surface_text(path: Path) -> str:
    """Plain text of a page / caption / voice file, via the language checker's loaders."""
    import check_language as cl  # noqa: WPS433
    suf = path.suffix.lower()
    if suf == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "glossary" in obj and "sections" in obj:
            obj = {k: v for k, v in obj.items() if k != "glossary"}  # definitions may name synonyms on purpose
        blocks = cl.blocks_from_json(obj, path.name)
        if isinstance(obj, dict) and isinstance(obj.get("claims"), list):
            blocks += [{"text": str(c.get("claim") or c.get("text") or "")} for c in obj["claims"]
                       if isinstance(c, dict)]
        return "\n".join(b["text"] for b in blocks)
    bl, _ = cl.load_blocks(path)
    return "\n".join(b["text"] for b in bl)


def _mentions(rx: re.Pattern, text: str) -> list[tuple[int, str]]:
    out = []
    for m in rx.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        if DEFINING.search(before):
            continue
        # skip matches inside back-ticked code: a code span is the code name, not prose
        if text[:m.start()].count("`") % 2 == 1:
            continue
        out.append((m.start(), m.group(0)))
    return out


def _example(text: str, pos: int) -> str:
    return re.sub(r"\s+", " ", text[max(0, pos - 50):pos + 60]).strip()


def analyse(surfaces: dict, glossary: list, aliases: dict, code_names: list, rules: dict | None = None,
            use_builtin: bool = True) -> dict:
    """surfaces: {"page": text, "captions": text, "voice": text, ...} -> report dict."""
    findings: list[dict] = []
    notes: list[dict] = []
    concepts: dict[str, dict] = {}
    for g in glossary or []:
        if not isinstance(g, dict) or not g.get("term"):
            continue
        t = str(g["term"])
        c = concepts.setdefault(t.lower(), {"term": t, "accepted": set(), "synonyms": set(), "code": set()})
        c["accepted"].update(str(a) for a in (g.get("aliases") or []))
        c["synonyms"].update(str(a) for a in (g.get("synonyms") or []))
        if g.get("code"):
            c["code"].update([g["code"]] if isinstance(g["code"], str) else [str(x) for x in g["code"]])
    for t, syns in (aliases or {}).items():
        c = concepts.setdefault(t.lower(), {"term": t, "accepted": set(), "synonyms": set(), "code": set()})
        c["synonyms"].update(syns)

    # ---- synonyms
    for key, c in concepts.items():
        term_forms = [c["term"]] + sorted(c["accepted"])
        term_use = {}
        for sname, text in surfaces.items():
            k = sum(len(_mentions(word_rx(f), text)) for f in term_forms)
            if k:
                term_use[sname] = k
        for syn in sorted(c["synonyms"]):
            if syn.lower() == c["term"].lower() or syn in c["accepted"]:
                continue
            use = {}
            ex = ""
            for sname, text in surfaces.items():
                ms = _mentions(word_rx(syn), text)
                if ms:
                    use[sname] = len(ms)
                    ex = ex or _example(text, ms[0][0])
            if use:
                where = ", ".join(f"{s} {n}x" for s, n in use.items())
                tw = ", ".join(f"{s} {n}x" for s, n in term_use.items()) or "nowhere yet"
                drift = sorted(set(use) | set(term_use)) if term_use else sorted(use)
                findings.append({"rule": "synonym", "severity": "review", "concept": c["term"], "found": syn,
                                 "surfaces": use, "term_surfaces": term_use, "example": ex,
                                 "message": f"{syn!r} ({where}) names the concept the glossary calls "
                                            f"{c['term']!r} ({tw}): use one term per concept on every surface"
                                            + (f" — the surfaces disagree: {', '.join(drift)}"
                                               if len(set(use) | set(term_use)) > 1 else "")})

    # ---- code names
    idents = set(code_names or [])
    for c in concepts.values():
        idents |= c["code"]
    for text in surfaces.values():
        for m in CODE_SPAN.finditer(text):
            v = m.group(1).strip()
            if structured_ident(v):
                idents.add(v.rstrip("()"))
    for ident in sorted(idents):
        parts = split_ident(ident)
        if len(parts) < 2 and not structured_ident(ident):
            continue
        joined = re.compile(r"(?<![A-Za-z0-9_])" + r"[\s_.\-]?".join(re.escape(p) for p in parts) +
                            r"(?![A-Za-z0-9_])", re.I)
        spaced = re.compile(r"(?<![A-Za-z0-9_])" + r"\s+".join(re.escape(p) for p in parts) + r"(?![A-Za-z0-9_])",
                            re.I)
        for sname, text in surfaces.items():
            for m in joined.finditer(text):
                got = m.group(0)
                if got == ident:
                    continue
                if " " in got:
                    continue  # handled by the spaced pass
                findings.append({"rule": "code_recased", "severity": "review", "concept": ident, "found": got,
                                 "surfaces": {sname: 1}, "example": _example(text, m.start()),
                                 "message": f"code name {ident!r} written as {got!r} on {sname}: keep code names "
                                            f"exactly as in the code, in monospace"})
            for m in spaced.finditer(text):
                got = m.group(0)
                words = got.split()
                if all(w[:1].isupper() for w in words):
                    findings.append({"rule": "code_translated", "severity": "review", "concept": ident,
                                     "found": got, "surfaces": {sname: 1}, "example": _example(text, m.start()),
                                     "message": f"code name {ident!r} translated to {got!r} on {sname}: write "
                                                f"`{ident}` exactly, or describe what it does in plain words"})
                else:
                    notes.append({"rule": "code_translated", "severity": "note", "concept": ident, "found": got,
                                  "surfaces": {sname: 1}, "example": _example(text, m.start()),
                                  "message": f"{got!r} on {sname} reads like the code name {ident!r} in prose; "
                                             f"fine as a description, but use `{ident}` when you mean the code"})

    # ---- built-in synonym sets (notes): only when no glossary concept already covers a word
    if use_builtin and rules:
        covered = set()
        for c in concepts.values():
            covered |= {c["term"].lower()} | {s.lower() for s in c["synonyms"]} | {a.lower() for a in c["accepted"]}
        for group in (rules.get("synonym_sets") or {}).get("sets", []):
            if any(w in covered for w in group):
                continue
            used = {}
            ex = ""
            for w in group:
                for sname, text in surfaces.items():
                    ms = _mentions(word_rx(w), text)
                    if ms:
                        used.setdefault(w, {})[sname] = len(ms)
                        ex = ex or _example(text, ms[0][0])
            if len(used) > 1:
                notes.append({"rule": "builtin_set", "severity": "note", "concept": "/".join(group),
                              "found": sorted(used), "surfaces": used, "example": ex,
                              "message": f"{' and '.join(repr(w) for w in sorted(used))} are both used: if they name "
                                         f"one concept, pick one and add it to the glossary"})
    return {"findings": findings, "notes": notes,
            "concepts": len(concepts), "code_names": sorted(idents)}


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 one-term-per-concept checker.")
    ap.add_argument("--glossary", default=None, help="glossary JSON or a page content.json with a glossary")
    ap.add_argument("--aliases", default=None, help="agent-written alias list")
    ap.add_argument("--page", action="append", default=[])
    ap.add_argument("--captions", action="append", default=[])
    ap.add_argument("--voice", action="append", default=[])
    ap.add_argument("--text", action="append", default=[], help="NAME=PATH, any other surface")
    ap.add_argument("--code-names", default=None, help="file with one code identifier per line")
    ap.add_argument("--no-builtin", action="store_true", help="skip the built-in synonym sets")
    args = ap.parse_args(argv)

    import check_language as cl  # noqa: WPS433
    paths: list[tuple[str, Path]] = []
    for kind in ("page", "captions", "voice"):
        for i, p in enumerate(getattr(args, kind)):
            paths.append((kind if i == 0 else f"{kind}{i + 1}", Path(p)))
    for spec in args.text:
        name, _, p = spec.partition("=")
        paths.append((name or "text", Path(p or name)))
    if not paths:
        emit({"gate": "terms", "status": "SKIPPED", "summary": "no page/captions/voice text supplied"})
        return EXIT_SKIPPED
    try:
        glossary = cl.load_glossary(args.glossary) if args.glossary else []
        aliases = load_aliases(Path(args.aliases)) if args.aliases else {}
        code_names = []
        if args.code_names:
            code_names = [l.strip() for l in Path(args.code_names).read_text(encoding="utf-8").splitlines()
                          if l.strip() and not l.startswith("#")]
        before, surfaces, missing = {}, {}, []
        for name, p in paths:
            if not p.exists():
                missing.append(str(p))
                continue
            before[str(p)] = sha(p)
            surfaces[name] = surface_text(p)
        rules = cl.load_rules()
    except Exception as e:  # noqa: BLE001
        emit({"gate": "terms", "status": "ERROR", "summary": f"bad input: {type(e).__name__}: {e}"})
        return EXIT_ERROR
    if not any(t.strip() for t in surfaces.values()):
        emit({"gate": "terms", "status": "SKIPPED", "missing": missing, "summary": "no text in the supplied files"})
        return EXIT_SKIPPED
    rep = analyse(surfaces, glossary, aliases, code_names, rules, use_builtin=not args.no_builtin)
    unchanged = all(sha(Path(k)) == v for k, v in before.items())
    n = len(rep["findings"])
    not_checked = []
    if not glossary and not aliases:
        not_checked.append("glossary/alias list (none supplied: only code names and built-in sets checked)")
    emit({"gate": "terms", "status": "REVIEW" if n else "PASS", "surfaces": sorted(surfaces),
          "concepts": rep["concepts"], "code_names": rep["code_names"][:100],
          "counts": {r: sum(1 for f in rep["findings"] + rep["notes"] if f["rule"] == r)
                     for r in ("synonym", "code_recased", "code_translated", "builtin_set")},
          "findings": rep["findings"][:200], "notes": rep["notes"][:200], "missing": missing,
          "inputs_unchanged": unchanged, "not_checked": not_checked,
          "summary": (f"{n} term finding(s) across {', '.join(sorted(surfaces))}" if n else
                      f"one term per concept holds across {', '.join(sorted(surfaces))}")
                     + (f"; {len(rep['notes'])} note(s)" if rep["notes"] else "")})
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
