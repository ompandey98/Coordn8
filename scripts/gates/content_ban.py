#!/usr/bin/env python3
"""
Coordn8 gate — CONTENT BANS (both deliverables).

Sweeps a FINAL payload (the HTML file, a narration script, any shipped text) for
content that must never appear: customer/case/order identifiers, likely person
names, emails, and project-specific jargon handles the agent supplies.

ADVISORY: every hit is a LEAD the agent clears by looking at it in context — a
regex cannot know that "Order" in prose is fine while "Order #10029" is not, so
it flags generously and the agent decides. Exit 1 (REVIEW) = "inspect these".

Usage:
    python3 scripts/gates/content_ban.py final.html
    python3 scripts/gates/content_ban.py final.html --jargon jargon.txt
    python3 scripts/gates/content_ban.py final.html --allow author_name

jargon.txt: one banned term/handle per line (internal codenames, etc.).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit)

# Patterns for identifiers that should never ship. Deliberately broad; hits are
# leads, not verdicts.
PATTERNS = {
    "order_or_case_id": re.compile(
        r"\b(?:order|case|ticket|invoice|acct|account|cust(?:omer)?|claim|ref)"
        r"[-_#:.\s]*#?\d{4,}\b", re.IGNORECASE),
    "long_bare_id": re.compile(r"\b[A-Z0-9]{2,}-?\d{6,}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "uuid": re.compile(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        re.IGNORECASE),
    "credit_card_like": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "phone_like": re.compile(r"\b\+?\d{1,3}[ -]?\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b"),
}

# Very light "person name" heuristic: "Firstname Lastname" with both capitalized.
# High false-positive rate on purpose — the agent confirms each. Skipped inside
# obvious code/URL contexts.
NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")

# Common two-word capitalized phrases that are NOT names — reduce obvious noise.
NAME_ALLOW = {
    "United States", "New York", "San Francisco", "Los Angeles", "Hong Kong",
    "Machine Learning", "Data Flow", "Source Code", "Decision Tree",
    "Getting Started", "Table Of", "Read Me", "Pull Request", "Unit Test",
}


def line_hits(text: str, jargon: list[str], allow: set[str]):
    findings = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for name, pat in PATTERNS.items():
            for m in pat.finditer(line):
                tok = m.group(0)
                if tok in allow:
                    continue
                findings.append({"line": i, "kind": name, "match": tok,
                                 "context": line.strip()[:120]})
        for m in NAME_RE.finditer(line):
            tok = m.group(0)
            if tok in NAME_ALLOW or tok in allow:
                continue
            findings.append({"line": i, "kind": "possible_person_name",
                             "match": tok, "context": line.strip()[:120],
                             "note": "heuristic — confirm it is a real name, not a phrase"})
        low = line.lower()
        for term in jargon:
            t = term.strip()
            if t and t.lower() in low and t not in allow:
                findings.append({"line": i, "kind": "jargon_handle",
                                 "match": t, "context": line.strip()[:120]})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--jargon", help="file with one banned term per line")
    ap.add_argument("--allow", nargs="*", default=[],
                    help="tokens to allow (e.g. an author name the user approved)")
    args = ap.parse_args()

    path = Path(args.payload)
    if not path.exists():
        emit({"gate": "content_ban", "status": "SKIPPED",
              "summary": f"no payload at {path}"})
        return EXIT_SKIPPED

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        emit({"gate": "content_ban", "status": "ERROR",
              "summary": f"cannot read {path}: {e}"})
        return EXIT_ERROR

    jargon: list[str] = []
    if args.jargon and Path(args.jargon).exists():
        jargon = [ln for ln in Path(args.jargon).read_text(
            encoding="utf-8").splitlines() if ln.strip()]

    findings = line_hits(text, jargon, set(args.allow))
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1

    emit({
        "gate": "content_ban",
        "status": "REVIEW" if findings else "PASS",
        "payload": str(path),
        "total_hits": len(findings),
        "by_kind": by_kind,
        "findings": findings[:200],  # cap the dump; count is exact above
        "truncated": len(findings) > 200,
        "summary": (
            f"{len(findings)} potential ban hit(s) to review across "
            f"{len(by_kind)} kind(s)" if findings
            else "no banned-content patterns found"
        ),
        "advisory_note": "Person-name and id heuristics over-flag on purpose. "
                         "Confirm each hit in context; clear false positives.",
    })
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
