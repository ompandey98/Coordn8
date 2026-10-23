#!/usr/bin/env python3
"""
Coordn8 gate — WORD TIMING (E2), advisory.

Checks a words.json (scripts/render/align.py output; also its `--js` form):

  - shape: {"lines": [{"name", "text", "start", "end", "words": [{"w", "start",
    "end", "i"}]}], "source", "fps"};
  - coverage: every display word of every line has a timing, in order (the
    display words are re-derived from the line's text, or from the beat map's
    text when --beatmap is given, with the same tokeniser the aligner uses);
  - monotonic: word starts never go backwards, start <= end, lines in order;
  - inside the window: every word lies inside its line's [start, end];
  - no word longer than 2 s;
  - accuracy (when --reference REF is given, a words.json-shaped file with the
    true or engine times): per-word start error in ms, mean / median / p90 /
    max and the share within the tolerance (default 80 ms). The accuracy check
    passes when every compared word is within tolerance (--criterion max), or
    when the chosen statistic is (--criterion mean|median|p90).

Exit codes (shared protocol): 0 PASS, 1 REVIEW (findings), 2 SKIPPED (no input),
3 ERROR (unreadable input).

Usage:
    python3 scripts/gates/check_word_timing.py _build/words.json [--beatmap _build/beatmap.json]
        [--reference ref_words.json] [--tolerance-ms 80] [--criterion max|mean|median|p90]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "render"))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
from align import display_words, norm_word  # noqa: E402

MAX_WORD_S = 2.0
EPS = 0.002


def load_words(path: Path) -> dict:
    txt = path.read_text(encoding="utf-8").strip()
    if txt.startswith("window.__C8_WORDS__"):
        txt = txt.split("=", 1)[1].strip().rstrip(";").strip()
    return json.loads(txt)


def _key(w) -> str:
    return norm_word(str(w)).casefold()


def check(words: dict, beatmap: dict | None = None) -> list:
    """Structural findings (list of strings); empty = clean."""
    f = []
    if not isinstance(words, dict) or not isinstance(words.get("lines"), list):
        return ["words.json has no lines[] array"]
    if words.get("source") not in ("engine", "onset", "mixed"):
        f.append(f"source is {words.get('source')!r}; expected engine|onset|mixed")
    if not isinstance(words.get("fps"), (int, float)) or not words.get("fps"):
        f.append("fps missing or zero")
    expected_text = {}
    if beatmap:
        for i, s in enumerate(beatmap.get("sentences") or beatmap.get("lines") or []):
            nm = str(s.get("name") or s.get("id") or f"s{s.get('idx', i + 1)}")
            expected_text[nm] = s.get("text", "")
        missing_lines = [n for n, t in expected_text.items() if display_words(t)
                         and n not in {ln.get("name") for ln in words["lines"]}]
        for n in missing_lines:
            f.append(f"line {n!r}: in the beat map but has no word timings")
    prev_line_start = -1.0
    for li, ln in enumerate(words["lines"]):
        nm = ln.get("name", f"#{li}")
        for k in ("name", "text", "start", "end", "words"):
            if k not in ln:
                f.append(f"line {nm!r}: missing key {k!r}")
        try:
            ls, le = float(ln.get("start")), float(ln.get("end"))
        except (TypeError, ValueError):
            f.append(f"line {nm!r}: start/end not numbers")
            continue
        if le < ls:
            f.append(f"line {nm!r}: end {le} before start {ls}")
        if ls < prev_line_start - EPS:
            f.append(f"line {nm!r}: starts at {ls}s, before the previous line — lines not in order")
        prev_line_start = ls
        ws = ln.get("words") or []
        want = display_words(expected_text.get(nm, ln.get("text", "")))
        got = [str(w.get("w", "")) for w in ws]
        if [_key(x) for x in got] != [_key(x) for x in want]:
            gk = [_key(x) for x in got]
            miss = [x for x in want if _key(x) not in gk]
            extra = [x for x in got if _key(x) not in {_key(y) for y in want}]
            f.append(f"line {nm!r}: timed words differ from the display words "
                     f"(missing {miss[:6]}, unexpected {extra[:6]}, {len(got)} timed vs {len(want)} display)")
        prev = ls - EPS
        for k, w in enumerate(ws):
            try:
                a, b = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                f.append(f"line {nm!r} word {k}: start/end missing or not numbers")
                continue
            if w.get("i") != k:
                f.append(f"line {nm!r} word {k} ({w.get('w')!r}): index i={w.get('i')!r}, expected {k}")
            if b < a:
                f.append(f"line {nm!r} word {k} ({w.get('w')!r}): end {b} before start {a}")
            if a < prev - EPS:
                f.append(f"line {nm!r} word {k} ({w.get('w')!r}): starts at {a}s, before the previous "
                         f"word ({prev}s) — not monotonic")
            if a < ls - EPS or b > le + EPS:
                f.append(f"line {nm!r} word {k} ({w.get('w')!r}): [{a}, {b}] outside the line window [{ls}, {le}]")
            if b - a > MAX_WORD_S + EPS:
                f.append(f"line {nm!r} word {k} ({w.get('w')!r}): lasts {b - a:.2f}s (> {MAX_WORD_S:.0f}s)")
            prev = max(prev, a)
    return f


def accuracy(words: dict, ref: dict, tol_ms: float = 80.0) -> dict:
    """Per-word start error against a reference words.json (matched by line name
    and word index; words must agree)."""
    rl = {ln.get("name"): ln for ln in ref.get("lines", [])}
    errs, unmatched, per_line = [], 0, []
    for ln in words.get("lines", []):
        r = rl.get(ln.get("name"))
        if not r:
            unmatched += len(ln.get("words") or [])
            continue
        le = []
        for w, rw in zip(ln.get("words") or [], r.get("words") or []):
            if _key(w.get("w")) != _key(rw.get("w")):
                unmatched += 1
                continue
            le.append(abs(float(w["start"]) - float(rw["start"])) * 1000.0)
        unmatched += abs(len(ln.get("words") or []) - len(r.get("words") or []))
        if le:
            per_line.append({"name": ln.get("name"), "mean_ms": round(statistics.mean(le), 1),
                             "max_ms": round(max(le), 1)})
        errs += le
    if not errs:
        return {"compared": 0, "unmatched": unmatched}
    e = sorted(errs)
    return {"compared": len(e), "unmatched": unmatched, "tolerance_ms": tol_ms,
            "mean_ms": round(statistics.mean(e), 1), "median_ms": round(statistics.median(e), 1),
            "p90_ms": round(e[min(len(e) - 1, int(0.9 * len(e)))], 1), "max_ms": round(e[-1], 1),
            "within_tolerance": round(sum(1 for x in e if x <= tol_ms) / len(e), 3), "per_line": per_line}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("words")
    ap.add_argument("--beatmap", default=None)
    ap.add_argument("--reference", default=None)
    ap.add_argument("--tolerance-ms", type=float, default=80.0)
    ap.add_argument("--criterion", choices=["max", "mean", "median", "p90"], default="max")
    a = ap.parse_args(argv)
    wp = Path(a.words)
    if not wp.exists():
        emit({"gate": "check_word_timing", "status": "SKIPPED", "summary": f"missing {wp} — no word timings built"})
        return EXIT_SKIPPED
    try:
        words = load_words(wp)
        bm = json.loads(Path(a.beatmap).read_text(encoding="utf-8")) if a.beatmap else None
        ref = load_words(Path(a.reference)) if a.reference else None
    except (OSError, ValueError) as ex:
        emit({"gate": "check_word_timing", "status": "ERROR", "summary": f"could not read input: {ex}"})
        return EXIT_ERROR
    findings = check(words, bm)
    acc = None
    if ref is not None:
        acc = accuracy(words, ref, a.tolerance_ms)
        if not acc.get("compared"):
            findings.append("accuracy: no word could be matched against the reference")
        else:
            stat = {"max": "max_ms", "mean": "mean_ms", "median": "median_ms", "p90": "p90_ms"}[a.criterion]
            if acc[stat] > a.tolerance_ms:
                findings.append(f"accuracy: {a.criterion} start error {acc[stat]} ms > ±{a.tolerance_ms:.0f} ms "
                                f"({acc['within_tolerance'] * 100:.0f}% of {acc['compared']} words within)")
            if acc.get("unmatched"):
                findings.append(f"accuracy: {acc['unmatched']} word(s) could not be matched to the reference")
    n = sum(len(ln.get("words") or []) for ln in words.get("lines", []))
    emit({"gate": "check_word_timing", "status": "REVIEW" if findings else "PASS",
          "words_json": str(wp), "lines": len(words.get("lines", [])), "words": n,
          "source": words.get("source"), "accuracy": acc, "findings": findings,
          "summary": (f"{len(findings)} word-timing issue(s)" if findings else
                      f"{n} words timed: complete, monotonic, inside their lines, none over "
                      f"{MAX_WORD_S:.0f}s" + (f"; {a.criterion} error {acc[{'max': 'max_ms', 'mean': 'mean_ms', 'median': 'median_ms', 'p90': 'p90_ms'}[a.criterion]]} ms"
                                              if acc and acc.get("compared") else "")),
          "advisory_note": "Measures timing structure (and error against a reference when given); "
                           "it does not judge whether a scene lands on the right word."})
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
