#!/usr/bin/env python3
"""
Coordn8 gate — CONTENT BANS (both deliverables).

Sweeps a FINAL payload (the HTML file, a narration script, any shipped text) for
content that must never appear: customer/case/order identifiers, likely person
names, emails, project-specific jargon handles the agent supplies, capability-
excuse/build-meta text, requester-personal framing (0.3a), and read-aloud raw
literals in narration (2.2a).

ADVISORY: every hit is a LEAD the agent clears by looking at it in context — a
regex cannot know that "Order" in prose is fine while "Order #10029" is not, so
it flags generously and the agent decides. Exit 1 (REVIEW) = "inspect these".

Shareability / correctness pattern classes (PLAYBOOK 0.3a / 2.2a / 0.3 / 2.6):
  - requester_personal_framing: the requester is the DIRECTOR, not the SUBJECT —
    flags lines that expose or only-make-sense-to the person who asked ("since
    you already know…", "the parts you didn't get", "if this gave you a
    headache", "your team/deadline/repo", "as you asked", "our chat"). The fix is
    to re-voice into a neutral statement about the system, or drop an irrelevant
    aside — never to lose a relevant fact. Generic teacherly "you" is NOT matched.
  - read_aloud_literal: a raw URL or long path the VOICE should not spell out
    (the frame carries the specific; the voice speaks its meaning). Strong lead
    on a narration script; weaker on HTML (a shown reference can be legitimate).
  - audience_roleplay_greeting: the audience (0.3) tailors the DESIGN — depth,
    entry point, pace, register — it is NOT a role the deliverable greets or
    role-plays. "Welcome to the team", "welcome aboard", "now that you've joined",
    "as a new hire" assume the reader IS one specific person joining one specific
    place; a hundred strangers share the same file. Flag every such salutation —
    the fix is to state what the system IS, not to greet whoever is watching.
  - acronym_expansion: factual correctness is non-negotiable (2.6) — NEVER invent
    an abbreviation's full form. This flags the ACT of expanding one ("X stands
    for…", "short for…", "(an acronym for…)") so EACH expansion is verified against
    a real source (docs/code) before it ships. The gate cannot know an expansion
    is wrong; it surfaces every expansion so a fabricated one can't slip through
    unverified. If the source doesn't define it, keep the abbreviation as-is.
  - stale_meta: raw source/build metadata leaking onto a frame or into narration
    — code-comment markers (TODO/FIXME/@author), version stamps (v1.2.3,
    "version 1.0"), license/copyright headers. None of it teaches; it reads as a
    machine dump (2.1a "no meta chrome"). Strong lead on narration/frames.
  - deck_chrome: presentation/slide-deck chrome that makes a crafted explainer
    look like a cheap exported PowerPoint (2.1a "no deck/slide chrome"). Slide or
    page numbers ("slide 3", "3 / 12", "page 4 of 20"), pagination language, and
    template title cards ("Agenda", "Next steps", "Thank you"). The video is one
    continuous film — there are no slides to number. Strong lead on a storyboard
    or on-frame text; the agent removes the numbering and conveys progress through
    the narrative/section beats instead. (A number that TEACHES — a count, rate,
    or value — is fine and is not what this targets.)
  - option_letter_talk: narrating in bare enumeration labels the audience doesn't
    share (PLAYBOOK 2.2b) — "option A does X", "approach B is faster", "in case 1
    … in case 2". Nobody talks in bare letters; it's the most robotic phrasing
    there is. The fix is to NAME the approach ("the cache-first approach") and let
    the name carry the sentence. A lead; the agent confirms it's a bare label, not
    a legitimate mention (a variable literally named `A`, an itemized list).
  - local_file_ref: the deliverable referencing the requester's LOCAL file/temp
    path as if the audience could see it (PLAYBOOK 2.2b / 0.3a) — "see the
    ABC_21July_temp file", "read it from my scratch folder". That pointer exists
    only on the requester's machine; a stranger sharing the video has no access.
    The fix is to describe what the artifact IS and how anyone obtains it ("the
    configuration the pipeline reads", "a snapshot you can generate"), keeping the
    file's CONTENT and dropping only the local pointer. Strong lead on narration.

Run it over the narration script too, not just the HTML, so a spoken personal
aside, a read-aloud URL, a greeting, an invented full-form, or a leaked version
stamp is caught even though it never appears as page text.

Usage:
    python3 scripts/gates/content_ban.py final.html
    python3 scripts/gates/content_ban.py Coordn8/_build/narration.txt
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
    # Capability-excuse / build-meta text must never reach the audience. A skip
    # is an operator status line, not deliverable content (PLAYBOOK 0.5 / 1.5).
    "capability_excuse": re.compile(
        r"\b(?:not\s+(?:supported|available|possible)\s+(?:in|on|for)\s+th(?:is|e)"
        r"|video\s+(?:is\s+)?(?:unavailable|not\s+available|skipped|could\s+not)"
        r"|unsupported\s+(?:in|on)\s+this"
        r"|could\s+not\s+(?:be\s+)?(?:generated|produced|rendered)\s+(?:on|in|here)"
        r"|this\s+(?:system|environment|machine)\s+(?:does\s+not|doesn't|can'?t)"
        r"|generated\s+by\s+coordn8"
        r"|ffmpeg\s+(?:is\s+)?(?:not|missing|unavailable))\b",
        re.IGNORECASE),
    # Requester-personal framing (PLAYBOOK 0.3a). The requester is the DIRECTOR,
    # not the SUBJECT — no line may expose or only-make-sense-to the person who
    # asked for the build: their knowledge state, situation, mood, or the chat
    # itself. Generic teacherly "you'll see this in foo.py" is fine and does NOT
    # match here; these target the requester's private context specifically.
    # Advisory leads — the agent re-voices to a neutral statement about the
    # system, or drops an irrelevant aside (never loses a relevant fact).
    "requester_personal_framing": re.compile(
        r"(?:"
        r"\bas\s+you\s+(?:asked|requested|mentioned|said|wanted|noted)"
        r"|\bsince\s+you\s+(?:already\s+)?(?:know|mentioned|asked|said|wanted)"
        r"|\byou\s+(?:already\s+)?(?:know|mentioned|asked\s+(?:me\s+)?(?:to|for|about)"
        r"|said|wanted|requested|struggled|struggle|were\s+(?:working|struggling|confused))"
        r"|\bthe\s+parts?\s+you\s+(?:didn'?t|do\s+not|don'?t|couldn'?t|already)"
        r"|\bgave\s+you\s+a\s+headache|\byou\s+had\s+a\s+headache|\byour\s+headache"
        r"|\byou\s+found\s+(?:it|this|that)\s+(?:hard|confusing|difficult)"
        r"|\bwe\s+(?:discussed|talked\s+about|agreed|said)"
        r"|\bour\s+(?:chat|conversation|discussion)"
        r"|\byour\s+(?:project|repo|repository|team|company|employer|manager|deadline|codebase)"
        r"|\byou\s+(?:mentioned|noted)\s+that"
        r")\b",
        re.IGNORECASE),
    # Read-aloud literal (PLAYBOOK 2.2a) — a raw URL/long path the VOICE should
    # not spell out. On the narration script these are strong leads; on the HTML
    # they're weaker (a shown reference can be legitimate), so the agent clears
    # each in context. The frame carries the specific; the voice speaks meaning.
    "read_aloud_literal": re.compile(
        r"(?:https?://\S+|(?:/[A-Za-z0-9._-]+){3,}/?)"),
    # Audience role-play / greeting (PLAYBOOK 0.3 / 0.3a). The audience tailors
    # DESIGN, never becomes a role the deliverable greets. These salutations
    # assume the reader is one specific person joining one specific place — the
    # exact opposite of a freely-shareable artifact. Flag every one; the fix is
    # to state what the SYSTEM is, not to greet whoever happens to be watching.
    "audience_roleplay_greeting": re.compile(
        r"(?:"
        r"\bwelcome\s+(?:to\s+the\s+(?:team|company|codebase|project|org)"
        r"|aboard|back)\b"
        r"|\bglad\s+(?:to\s+have\s+you|you'?re\s+here|you\s+(?:joined|could\s+join))"
        r"|\bnow\s+that\s+you(?:'?ve|\s+have)\s+joined"
        r"|\b(?:as|now\s+that\s+you'?re)\s+(?:a\s+)?new\s+(?:hire|team\s+member|"
        r"member\s+of\s+the\s+team|engineer\s+(?:here|on\s+the\s+team|joining))"
        r"|\bon\s+your\s+first\s+day"
        r"|\bthanks\s+for\s+joining\s+(?:the\s+team|us)"
        r"|\bhappy\s+to\s+have\s+you\s+on\s+(?:the\s+)?(?:team|board)"
        r")\b",
        re.IGNORECASE),
    # Acronym / abbreviation expansion (PLAYBOOK 2.6 factual correctness). This
    # flags the ACT of expanding an abbreviation so the agent VERIFIES each one
    # against a real source (docs/code) before shipping — a fabricated full-form
    # is a factual defect. A regex cannot know an expansion is wrong; it surfaces
    # every expansion so none slips through unverified. If the source doesn't
    # define it, the rule is: keep the abbreviation as-is (do not guess).
    "acronym_expansion": re.compile(
        r"(?:"
        r"\bstands?\s+for\b"
        r"|\bshort\s+for\b"
        r"|\babbreviat(?:ion|ed|es)\s+(?:for|of)\b"
        r"|\backronym\s+(?:for|of|standing\s+for)\b"
        r"|\bwhich\s+stands\s+for\b"
        r"|\bexpands?\s+to\b"
        # "ABC (Anything Case Blah)" — an ALL-CAPS token immediately followed by a
        # parenthetical Title-Case expansion is the classic invented full-form.
        r"|\b[A-Z]{2,6}\s*\((?=[^)]*[A-Z][a-z])[^)]{3,60}\)"
        r")",
        re.IGNORECASE),
    # Stale build/source metadata (PLAYBOOK 2.1a "no meta chrome" / 1.5). Code
    # comment markers, version stamps, and license headers teach nothing and read
    # as a machine dump if they land on a frame or in narration. Strong lead there;
    # on the page a version in the footer is legitimate, so the agent clears each.
    "stale_meta": re.compile(
        r"(?:"
        r"\b(?:TODO|FIXME|XXX|HACK|NOTE|BUG)\b\s*[:(]"
        r"|@(?:author|param|returns?|deprecated|since|version)\b"
        r"|\bv\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?\b"
        r"|\bversion\s+\d+\.\d+"
        r"|\b(?:copyright|\(c\)|©)\s+\d{4}"
        r"|\ball\s+rights\s+reserved\b"
        r"|\blicensed\s+under\s+the\b"
        r")",
        re.IGNORECASE),
    # Deck / slide chrome (PLAYBOOK 2.1a "no deck/slide chrome"). Burned-in slide
    # or page numbers, pagination, and template title cards make a crafted film
    # read as a cheap exported slideshow. Targets the numbering/pagination FORMS
    # specifically — a number that teaches (a count, rate, or value) is NOT
    # matched. Strong lead on a storyboard or on-frame text; the fix is to remove
    # the pagination and convey progress through the narrative/section beats.
    "deck_chrome": re.compile(
        r"(?:"
        r"\b(?:slide|page)\s*#?\s*\d+\s*(?:of|/)\s*\d+\b"     # "slide 3 of 12", "page 4/20"
        r"|\b(?:slide|page)\s+(?:no\.?|number)\s*\d+\b"        # "slide no. 3"
        r"|\bslide\s+\d+\b"                                    # "slide 3"
        r"|\b\d+\s*/\s*\d+\b(?=\s*(?:slides?|pages?)?\s*$)"    # bare "3 / 12" as a footer
        r"|\bnext\s+slide\b|\bprevious\s+slide\b"
        r"|\bagenda\b|\btable\s+of\s+contents\b"
        r"|\bthank\s+you\s*[!.]*\s*$|\bthanks\s+for\s+watching\b|\bany\s+questions\b"
        r")",
        re.IGNORECASE),
    # Bare option-letter talk (PLAYBOOK 2.2b): narrating in enumeration labels the
    # audience doesn't share — "option A does X", "approach B is faster". Match the
    # keyword case-insensitively, but require an UPPERCASE single letter A–E (so the
    # article "a" is never flagged) or a small digit, standing alone as a bare
    # label. Lead: the agent NAMES the approach instead of talking in letters.
    "option_letter_talk": re.compile(
        r"\b(?i:option|approach|method|alternative|choice|scenario|variant|strategy|case)"
        r"\s+(?:[A-E]|[1-9])\b(?![-:=\w])"),
    # Local-file reference (PLAYBOOK 2.2b / 0.3a): the deliverable pointing at the
    # requester's LOCAL/temp/scratch file or machine as if the audience could see
    # it. Targets temp/scratch/dated-local pointers and "my/your folder"
    # directions ONLY — NEVER normal source-citation refs (router.py:40), which
    # are legitimate traceability. Lead: describe what the artifact IS and how
    # anyone obtains it; keep the file's content, drop the local pointer.
    "local_file_ref": re.compile(
        r"(?:"
        # temp/scratch/tmp filename with a data extension
        r"\b[\w-]*(?:temp|tmp|scratch)[\w-]*\.(?:json|txt|csv|md|log|xml|ya?ml|dat|xlsx?)\b"
        # "temp file", "scratch folder", etc.
        r"|\b(?:temp|tmp|scratch)\s+(?:file|folder|dir|directory)\b"
        # a filename token carrying a month name (the ABC_21July_temp shape)
        r"|\b\w+_\d{0,2}\s?(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*(?:_\w+)?\b"
        # explicit local-machine directions
        r"|\b(?:on|in|from|under)\s+(?:my|your)\s+(?:local\s+)?(?:folder|directory|machine|laptop|desktop|downloads?|home|scratch)\b"
        r"|\b(?:my|your)\s+local\s+(?:folder|directory|files?|machine|copy|path)\b"
        r")",
        re.IGNORECASE),
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


def _blank_code_blocks(text: str) -> str:
    """Blank <style>/<script> bodies (keeping line numbers); keep JSON data blocks."""
    def blank(m):
        if m.group(1).lower() == "script" and "application/json" in (m.group(2) or "").lower():
            return m.group(0)
        body = m.group(3)
        return m.group(0).replace(body, "\n" * body.count("\n"), 1) if body else m.group(0)
    return re.sub(r"<(style|script)(\s[^>]*)?>(.*?)</\1>", blank, text, flags=re.S | re.I)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("payload")
    ap.add_argument("--jargon", help="file with one banned term per line")
    ap.add_argument("--skip-code", action="store_true",
                    help="HTML only: ignore the bodies of <style> and executable <script> blocks "
                         "(JSON data blocks are still swept). Use on template-built pages, whose "
                         "inline CSS/JS is not reader-facing text. Off by default.")
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

    if args.skip_code and path.suffix.lower() in (".html", ".htm"):
        text = _blank_code_blocks(text)
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
