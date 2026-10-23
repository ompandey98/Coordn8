#!/usr/bin/env python3
"""
Coordn8 gate — TRUTH TESTS (advisory, D3): an unbacked claim must be marked as an estimate or cited.

A sentence makes a CLAIM when it carries
  metric       a number with a unit, a percentage or a multiplier ("40% faster", "3x", "200 ms")
  comparative  a comparison with a number or a "than" ("faster than the old path")
  absolute     always / never / guaranteed / zero downtime / instantly ...
  superlative  fastest / best / the only / industry-leading ...
  count        a bare count of things ("handles 5 outcomes") — a NOTE: usually code-derived
and it is BACKED when it carries
  * an inline citation: a `path:line` reference, a [[path:line]] chip, a claim id ([c3], {claim:c3},
    data-claim="c3") that exists in the claims manifest, or a footnote marker [^1];
  * a matching entry in the claims manifest (`--claims claims.json`, or the `claims` list of a page
    content.json): same number, or at least half its content words, with a `ref` or an inferred mark;
  * an estimate marker in the same sentence: about / roughly / approximately / ~ / "(estimate)" /
    "(inferred)" ... — an honest estimate passes.
Otherwise the finding says: mark it as an estimate, or cite it. Imperatives ("Never delete the
cache") are instructions, not claims, and are skipped. A claim id that is not in the manifest is
flagged (a citation that points nowhere is worse than none).

It REPORTS; it never rewrites. Inputs are hashed before and after to prove they were not touched.

Usage:
    python3 scripts/gates/check_truth.py page_content.json
    python3 scripts/gates/check_truth.py narration.txt draft.md --claims _build/claims.json
Exit: 0 PASS (notes allowed) · 1 REVIEW · 2 SKIPPED · 3 ERROR.
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
import check_language as cl  # noqa: E402

NUMWORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
            "sixty|seventy|eighty|ninety|hundred|thousand|half|double|triple")
FILE_LINE = re.compile(r"(?<![\w/])[\w./-]+\.[A-Za-z0-9]{1,8}:\d+(?:-\d+)?\b")
CHIP = re.compile(r"\[\[[^\]]+:\d+(?:-\d+)?\]\]")
CLAIM_ID = re.compile(r"\[(?:claim[:\s-]?)?(c[\w-]*\d[\w-]*)\]|\{claim:([\w-]+)\}|data-claim=[\"']([\w-]+)[\"']", re.I)
FOOTNOTE = re.compile(r"\[\^[\w-]+\]")
SOURCE_PHRASE = re.compile(r"\((?:source|measured|see|per)\b[^)]*\)|\baccording\s+to\s+(?:the\s+)?(?:benchmark|"
                           r"measurement|log|report|test)s?\b", re.I)
STOP = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "it", "its", "this", "that", "with",
        "by", "as", "at", "be", "from", "than", "per", "so", "we", "you"}


class Truth:
    def __init__(self, rules: dict):
        t = rules["truth"]
        self.est = [m.lower() for m in t["estimate_markers"]]
        self.soft = set(t["absolute_softeners"])
        self.abs = sorted((a.lower() for a in t["absolutes"]), key=len, reverse=True)
        self.sup = sorted((s.lower() for s in t["superlatives"]), key=len, reverse=True)
        self.comp = sorted((c.lower() for c in t["comparatives"]), key=len, reverse=True)
        units = sorted(t["units"], key=len, reverse=True)
        self.metric = re.compile(
            r"(?<![\w.])(?:[$€£]\s?\d[\d,.]*\s?(?:k|m|million|billion)?|\d[\d,.]*\s?(?:%|percent\b|per\s+cent\b|×)|"
            r"\d[\d,.]*\s?(?:" + "|".join(re.escape(u) for u in units) + r")(?![A-Za-z])|"
            r"(?:" + NUMWORDS + r")\s+(?:percent|per\s+cent|times\s+(?:faster|slower|cheaper|more|less|fewer)))",
            re.I)
        self.count = re.compile(r"(?<![\w.:/#-])\d{1,6}(?:,\d{3})*(?![\w.:%/-])\s+[a-z]{3,}", re.I)
        self.lex = cl.Lex(rules)

    def phrase(self, low: str, p: str) -> bool:
        return cl.has_phrase(low, p)


def _norm_nums(s: str) -> set:
    return {re.sub(r"[,\s]", "", m.group(0)).lower() for m in re.finditer(r"\d[\d,.]*\s?%?", s)}


def _content(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


def load_claims(obj) -> list[dict]:
    if isinstance(obj, dict):
        obj = obj.get("claims") or []
    out = []
    for c in obj or []:
        if isinstance(c, dict):
            text = str(c.get("text") or c.get("claim") or "")
            backed = bool(c.get("ref")) or c.get("inferred") is True or \
                str(c.get("status") or c.get("basis") or "").lower() in ("inferred", "read", "estimate")
            out.append({"id": str(c.get("id") or ""), "text": text, "backed": backed, "ref": c.get("ref")})
    return out


def is_instruction(sentence: str, tr: Truth) -> bool:
    ws = re.findall(r"[A-Za-z][A-Za-z'’-]*", sentence)
    if len(ws) >= 2 and ws[0].lower() in ("never", "always", "do", "don't"):
        nxt = ws[1].lower() if ws[0].lower() in ("never", "always") else (ws[2].lower() if len(ws) > 2 else "")
        if ws[0].lower() == "do" and ws[1].lower() != "not":
            return False
        return nxt in tr.lex.verbs
    return False


def analyse(blocks: list[dict], claims: list[dict], rules: dict) -> dict:
    tr = Truth(rules)
    ids = {c["id"].lower() for c in claims if c["id"]}
    findings, notes, backed_ok = [], [], []
    for b in blocks:
        text = str(b.get("text") or "")
        for s in cl.split_sentences(text):
            low = s.lower()
            masked, _ = cl.mask_code(s)
            mlow = masked.lower()
            triggers = []
            for m in tr.metric.finditer(masked):
                if "CODETOKEN" in m.group(0):
                    continue
                triggers.append(("metric", m.group(0).strip()))
            for c in tr.comp:
                if tr.phrase(mlow, c):
                    after = mlow.split(c, 1)[1][:6] if c in mlow else ""
                    if any(k == "metric" for k, _ in triggers) or after.strip().startswith("than"):
                        triggers.append(("comparative", c))
                        break
            if not is_instruction(s, tr):
                for a in tr.abs:
                    mm = re.search(r"(?<![a-z0-9])" + re.escape(a) + r"(?![a-z0-9])", mlow)
                    if mm:
                        prev = re.findall(r"[a-z']+", mlow[:mm.start()])
                        if prev and prev[-1] in tr.soft:
                            continue
                        triggers.append(("absolute", a))
                        break
            for sp in tr.sup:
                if tr.phrase(mlow, sp):
                    triggers.append(("superlative", sp))
                    break
            counts = [m.group(0) for m in tr.count.finditer(masked)
                      if not re.match(r"(?i)\d+\s+(?:of|to|and|or|in|on|at|by|is|are|was)\b", m.group(0))]
            if not triggers and not counts:
                continue
            # ---- backing
            cited = bool(FILE_LINE.search(s) or CHIP.search(s) or FOOTNOTE.search(s) or SOURCE_PHRASE.search(s))
            bad_ids = []
            for m in CLAIM_ID.finditer(s):
                cid = next(g for g in m.groups() if g)
                if ids and cid.lower() not in ids:
                    bad_ids.append(cid)
                else:
                    cited = True
            estimate = any(tr.phrase(low, e) for e in tr.est)
            matched = None
            if not cited and claims:
                nums = _norm_nums(masked)
                cw = _content(masked)
                for c in claims:
                    if not c["backed"]:
                        continue
                    cn = _norm_nums(c["text"])
                    if nums and cn and nums & cn and len(cw & _content(c["text"])) >= 1:
                        matched = c
                        break
                    cc = _content(c["text"])
                    if cc and len(cw & cc) >= max(2, (len(cc) + 1) // 2):
                        matched = c
                        break
            where = b.get("where") or "text"
            for cid in bad_ids:
                findings.append({"rule": "unknown_claim_id", "severity": "review", "where": where, "sentence": s[:240],
                                 "claim_id": cid,
                                 "message": f"cites claim {cid!r}, which is not in the claims manifest: fix the id "
                                            f"or add the claim with its file:line"})
            if cited or matched or estimate:
                if triggers:
                    backed_ok.append({"where": where, "sentence": s[:160],
                                      "by": "citation" if cited else ("claims manifest " + (matched or {}).get("id", "")
                                                                      if matched else "estimate marker")})
                continue
            if triggers:
                kind, trig = triggers[0]
                findings.append({"rule": f"unbacked_{kind}", "severity": "review", "where": where,
                                 "sentence": s[:240], "trigger": trig,
                                 "triggers": [{"kind": k, "text": t} for k, t in triggers],
                                 "message": f"unbacked {kind} claim ({', '.join(repr(t) for _, t in triggers)}): "
                                            f"mark it as an estimate ('about …', '(estimate)') or cite it "
                                            f"(file:line or a claims-manifest entry)"})
            if not triggers:
                for c in counts[:3]:
                    notes.append({"rule": "unbacked_count", "severity": "note", "where": where, "sentence": s[:240],
                                  "trigger": c,
                                  "message": f"count {c!r} has no citation: if it comes from the code, cite the "
                                             f"line; if it is a guess, say so"})
    return {"findings": findings, "notes": notes, "backed": backed_ok}


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 truth tests (report only).")
    ap.add_argument("inputs", nargs="*")
    ap.add_argument("--claims", default=None, help="claims manifest (facts_trace shape)")
    args = ap.parse_args(argv)
    if not args.inputs:
        emit({"gate": "truth", "status": "SKIPPED", "summary": "no input supplied"})
        return EXIT_SKIPPED
    try:
        rules = cl.load_rules()
        blocks, claims, before, missing = [], [], {}, []
        for inp in args.inputs:
            p = Path(inp)
            if not p.exists():
                missing.append(inp)
                continue
            before[inp] = sha(p)
            bl, extra = cl.load_blocks(p)
            blocks += bl
            claims += load_claims(extra.get("claims") or [])
        if args.claims and Path(args.claims).exists():
            before[args.claims] = sha(Path(args.claims))
            claims += load_claims(json.loads(Path(args.claims).read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        emit({"gate": "truth", "status": "ERROR", "summary": f"bad input: {type(e).__name__}: {e}"})
        return EXIT_ERROR
    if not blocks:
        emit({"gate": "truth", "status": "SKIPPED", "missing": missing, "summary": "no text in the input(s)"})
        return EXIT_SKIPPED
    rep = analyse(blocks, claims, rules)
    unchanged = all(sha(Path(k)) == v for k, v in before.items())
    n = len(rep["findings"])
    counts: dict[str, int] = {}
    for f in rep["findings"] + rep["notes"]:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    emit({"gate": "truth", "status": "REVIEW" if n else "PASS", "claims_in_manifest": len(claims),
          "counts": counts, "findings": rep["findings"][:200], "notes": rep["notes"][:200],
          "backed": rep["backed"][:200], "inputs_unchanged": unchanged, "missing": missing,
          "not_checked": ["whether a cited line actually SUPPORTS the claim (read it: facts_trace resolves the "
                          "reference, a reader judges the support)"],
          "summary": (f"{n} unbacked claim(s): mark each as an estimate or cite it" if n else
                      f"every claim-shaped sentence is cited or marked as an estimate ({len(rep['backed'])} backed)")
                     + (f"; {len(rep['notes'])} note(s)" if rep["notes"] else "")})
    return EXIT_REVIEW if n else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
