#!/usr/bin/env python3
"""
Coordn8 — PRONUNCIATION: give the VOICE a spoken form without touching what the
EYE reads.

The problem this solves
-----------------------
Local TTS engines read short initialisms as if they were words: "SOP" comes out
as "sop", "POD" as "pod", "SLA" as "slaa". The narration is unwatchable and
instantly reads as machine-generated.

The trap that makes the naive fix WRONG
----------------------------------------
In Coordn8 the narration string is used for THREE things at once — it is the TTS
input, it is the caption text, AND (via the beat map, 2.3/2.3a) the subtitle
gate enforces that the captions equal it *verbatim*. So you must NOT rewrite
"SOP" -> "S O P" in the narration itself: that would make the on-screen captions
and the page literally read "S O P", which is wrong. The eye should read **SOP**;
only the **voice** should say the letters.

The fix: TWO forms, one source
-------------------------------
Keep the clean DISPLAY form (`text`) exactly as authored — it is what the caption
and the page show. Derive a separate SPOKEN form (`speech`) that ONLY the TTS
engine consumes. This module derives that spoken form deterministically:

  - An **initialism** (letters said one at a time — SOP, POD, API, SLA, RBAC) is
    spelled out so the voice says the letters. In SSML mode we emit the correct
    standard: ``<say-as interpret-as="characters">SOP</say-as>``. In plain-text
    mode (engines that ignore SSML, e.g. macOS `say`, espeak) we space the
    letters — "S O P" — which those engines read letter-by-letter.
  - A **word-acronym** (pronounced AS a word — NASA, JSON, YAML, REST, LASER) is
    LEFT ALONE. Spelling those would introduce the opposite bug ("N A S A").
  - Anything ambiguous for THIS project (is "SQL" "sequel" or "S-Q-L"? is "GUI"
    "gooey" or "G-U-I"?) is decided by the agent via the override lists — the
    engine ships a sensible default and an escape hatch, exactly like every other
    Coordn8 helper (PLAYBOOK prime directive: advisory, never a straitjacket).

This module is deterministic and dependency-free, so the tests can prove the
spoken form spells while the caption/page form stays clean.

Usage:
  # transform a run of text (plain spoken form on stdout)
  python3 scripts/render/pronounce.py text "Follow the SOP; NASA uses JSON."
      -> Follow the S O P; NASA uses JSON.

  # SSML spoken form (for edge-tts / any SSML-capable engine)
  python3 scripts/render/pronounce.py text "Follow the SOP." --ssml
      -> Follow the <say-as interpret-as="characters">SOP</say-as>.

  # add a `speech` field to every beatmap sentence whose spoken form differs from
  # its display `text` (TTS reads `speech`; captions/page keep `text`)
  python3 scripts/render/pronounce.py beatmap _build/beatmap.json --out _build/beatmap.json

  # project overrides (agent decides): force-spell some, force-word others
  python3 scripts/render/pronounce.py text "..." --spell SQL,GUI --word POD
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Caption marks (⟦key:…⟧) live in the DISPLAY text only. They are stripped before the
# spoken form is derived, so the voice engine never reads a bracket (E1). Identity on
# text with no marks, so every pre-existing behaviour below is unchanged.
from caption_marks import strip as strip_marks  # noqa: E402


# Acronyms that are pronounced AS A WORD — never spelled out. Conservative on
# purpose: only entries that are overwhelmingly said as words in normal speech.
# Anything genuinely split by convention (SQL, GUI, JPEG, GIF, …) is intentionally
# NOT here — it is left to the project's own --word/--spell override, because the
# right answer differs by shop and only the agent knows the project's convention.
WORD_ACRONYMS = frozenset({
    # organizations / names commonly voiced as words
    "NASA", "NATO", "NAFTA", "UNICEF", "UNESCO", "OPEC", "FIFA",
    # tech formats / stacks said as words
    "JSON", "YAML", "TOML", "REST", "SOAP", "CRUD", "GRPC",
    "SAAS", "PAAS", "IAAS", "FAAS",
    # networking / computing said as words
    "LAN", "WAN", "VLAN", "WLAN", "ASCII", "WYSIWYG", "CAPTCHA",
    # genuine word-acronyms from general English
    "LASER", "RADAR", "SONAR", "SCUBA", "AWOL", "PIN",
    # short English words that happen to be all-caps in tech prose
    "OK",
})

# A token is an INITIALISM candidate when it is 2–5 ASCII capital letters. Common
# spelled initialisms are 2–5 letters (SOP, API, SLA, RBAC, HTTPS); 6+ all-caps
# runs are overwhelmingly words/names/filenames (README, CONFIG, CHANGELOG,
# SCOTUS) and are left alone. A rare longer initialism is handled by --spell.
_INITIALISM = re.compile(r"^[A-Z]{2,5}$")

# Split text into tokens while KEEPING separators, so we can rebuild the string
# with original spacing/punctuation intact. A "word" here is a maximal run of
# letters/digits; everything else (spaces, punctuation) is preserved verbatim.
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def _norm_key(token: str) -> str:
    return token.upper()


def is_initialism(core: str, *, force_spell: set[str], force_word: set[str]) -> bool:
    """Should `core` (a bare alpha[+digit] word, no trailing 's') be spelled out?

    Precedence: explicit project overrides win, then the word-acronym allowlist
    (leave alone), then the shape heuristic (2–6 all-caps letters -> spell).
    """
    key = _norm_key(core)
    if key in force_spell:
        return True
    if key in force_word:
        return False
    if key in WORD_ACRONYMS:
        return False
    return bool(_INITIALISM.match(core))


def _spell_plain(letters: str) -> str:
    """"SOP" -> "S O P" (space-separated letters; engines read them one by one)."""
    return " ".join(letters)


def _spell_ssml(letters: str) -> str:
    """"SOP" -> the standard SSML that instructs any compliant engine to spell it."""
    return f'<say-as interpret-as="characters">{letters}</say-as>'


def spoken_word(word: str, *, ssml: bool, force_spell: set[str],
                force_word: set[str]) -> str:
    """Return the spoken form of ONE matched word token (may equal it unchanged).

    Handles a trailing plural/possessive-ish lowercase 's' (SOPs, APIs): the
    letters are spelled and the 's' is kept so the voice says "…-s"."""
    core, suffix = word, ""
    if len(word) > 2 and word[-1] == "s" and word[:-1].isupper():
        core, suffix = word[:-1], "s"  # SOPs -> core "SOP" + "s"

    if not is_initialism(core, force_spell=force_spell, force_word=force_word):
        return word  # word-acronym or not an initialism → say as written

    spelled = _spell_ssml(core) if ssml else _spell_plain(core)
    if not suffix:
        return spelled
    # keep the plural attached so it isn't read as a stray letter: "S O Ps"
    return f"{spelled}{suffix}"


def to_speech(text: str, *, ssml: bool = False, spell: set[str] | None = None,
              word: set[str] | None = None) -> str:
    """Derive the SPOKEN form of a narration string. `text` is never mutated in
    place — this returns a new string for the TTS engine to consume.

    Caption marks are removed FIRST: the spoken form never carries display markup."""
    text = strip_marks(text)
    force_spell = {s.upper() for s in (spell or set())}
    force_word = {w.upper() for w in (word or set())}

    def repl(m: re.Match) -> str:
        return spoken_word(m.group(0), ssml=ssml,
                           force_spell=force_spell, force_word=force_word)

    return _WORD.sub(repl, text)


def transform_beatmap(path: Path, out: Path, *, ssml: bool,
                      spell: set[str] | None, word: set[str] | None) -> int:
    """Add a `speech` field to each sentence whose spoken form differs from its
    display `text`. The `text` (caption/page source of truth) is left untouched,
    so the subtitle gate still sees the verbatim spoken-vs-caption contract on the
    DISPLAY text, and only the TTS step reads `speech`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    sentences = data.get("sentences") or []
    changed = 0
    for s in sentences:
        disp = str(s.get("text", ""))
        if not disp:
            continue
        spoken = to_speech(disp, ssml=ssml, spell=spell, word=word)
        if spoken != disp:
            s["speech"] = spoken
            changed += 1
        elif "speech" in s and s["speech"] == disp:
            # keep the file clean: drop a redundant speech==text field
            del s["speech"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    sys.stderr.write(
        f"[coordn8:pronounce] {changed}/{len(sentences)} sentence(s) got a spoken "
        f"form; display text unchanged -> {out}\n")
    return 0


def _split_csv(v: str | None) -> set[str]:
    return {p.strip() for p in v.split(",")} if v else set()


def main() -> int:
    ap = argparse.ArgumentParser(description="Coordn8 pronunciation helper.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("text", help="print the spoken form of a string")
    t.add_argument("text", nargs="?", help="text (omit to read stdin)")
    t.add_argument("--ssml", action="store_true")
    t.add_argument("--spell", help="comma-list of tokens to FORCE-spell")
    t.add_argument("--word", help="comma-list of tokens to FORCE-say-as-word")

    b = sub.add_parser("beatmap", help="add a `speech` field to beatmap sentences")
    b.add_argument("beatmap")
    b.add_argument("--out", required=True)
    b.add_argument("--ssml", action="store_true")
    b.add_argument("--spell")
    b.add_argument("--word")

    args = ap.parse_args()
    if args.cmd == "text":
        text = args.text if args.text is not None else sys.stdin.read()
        sys.stdout.write(to_speech(text, ssml=args.ssml,
                                   spell=_split_csv(args.spell),
                                   word=_split_csv(args.word)))
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    if args.cmd == "beatmap":
        return transform_beatmap(
            Path(args.beatmap), Path(args.out), ssml=args.ssml,
            spell=_split_csv(args.spell), word=_split_csv(args.word))
    return 1


if __name__ == "__main__":
    sys.exit(main())
