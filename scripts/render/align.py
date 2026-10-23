#!/usr/bin/env python3
"""Coordn8 — WORD-LEVEL TIMING for the narration (E2), computed on this machine.

Every display word of every narration line gets a start and an end time, so a
scene can land a reveal on the exact word it depicts (PLAYBOOK 2.3) instead of
on the sentence start. Nothing is sent anywhere: no hosted transcription, no
network. Two local sources, best first:

  engine   the voice engine's own timings (the neural engine reports phoneme
           durations, grouped into words by scripts/voice/render.py). Spoken-form
           tokens are mapped back to DISPLAY words: an initialism the voice was
           given as "S O P" is the one display word "SOP"; markup is stripped.
  onset    a signal-based fallback for engines that report nothing:
             1. short-time energy (25 ms window, 10 ms hop) and an onset curve
                (spectral flux when numpy is present, energy rise otherwise);
             2. speech regions between long silences (>= 100 ms), each start
                refined to the sample where a 2 ms envelope rises — no word may
                start inside a long silence;
             3. words are assigned to regions by a monotonic dynamic programme
                over expected durations (a duration model of the SPOKEN form:
                syllables by a vowel-group heuristic, letters, closed-class
                words shorter), preferring region breaks at punctuation;
             4. inside a region the words are spread by that expected duration
                and every start is placed on an onset: "joint" (default) picks
                all starts of the region together on the 10 ms grid (squared
                log ratio of each word's duration to its share, minus a reward
                for an onset peak / an energy dip at the start); "greedy" (the
                SPEC 4 baseline) snaps each start in turn to the nearest onset
                peak within +-120 ms and re-spreads the rest from there.
           pure Python when numpy is absent (set C8_ALIGN_NO_NUMPY=1 to force it).
           On continuous speech the fallback does NOT hold every word within
           +-80 ms; measure it with check_word_timing.py --reference.

Output, `words.json` (read by the scene runtime as window.__C8_WORDS__):

    {"lines": [{"name", "text", "start", "end",
                "words": [{"w", "start", "end", "i"}]}],
     "source": "engine|onset|mixed", "fps": N}

  * times are ABSOLUTE seconds (line offset from the beat map + time in the line);
  * `text` is the display text with markup stripped; `w` is the display word
    with its edge punctuation removed ("merge;" -> "merge"); `i` is the word's
    0-based index in the line. Tokens with no letter or digit are not words.

Lookup semantics (mirrored by `Words` below and by atLine/atWord in the scene
runtime): a line is found by its exact name; a word by case-insensitive match
of its edge-stripped form, optionally the n-th occurrence, optionally a
multi-word phrase (consecutive words). A miss THROWS — it never falls back to
an estimate, so deleting a word from the narration breaks the scene loudly.

CLI:
    python3 scripts/render/align.py _build/beatmap.json --audio-dir _build/voice \
        --out _build/words.json [--results voice_results.json] \
        [--prefer engine|onset] [--js [PATH]]

Beat map lines: {"idx", "text", "start", "end", "speech"?, "name"|"id"?,
"wav"?, "word_timings"?}. `word_timings` (and those in --results) are times
inside the line's own wav, as render.py returns them. Audio for a line is found
at `wav`, else <audio-dir>/<name>.wav, <id>.wav, or line<NNN>.wav (render.py's
default name for the line at 0-based position NNN of the beat map).
Exit codes: 0 written, 3 a line could not be timed (named on stderr).
"""
from __future__ import annotations

import argparse
import array
import difflib
import json
import math
import os
import re
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    from pronounce import to_speech  # noqa: E402
except Exception:  # pragma: no cover - pronounce.py ships beside this file
    to_speech = None

try:  # numpy is optional: the pure-Python path gives the same contract
    if os.environ.get("C8_ALIGN_NO_NUMPY") == "1":
        raise ImportError("disabled by C8_ALIGN_NO_NUMPY")
    import numpy as np
except Exception:  # pragma: no cover - exercised by the tests with the env switch
    np = None

HOP_S = 0.010              # 10 ms hop
WIN_S = 0.025              # 25 ms analysis window
LONG_SILENCE_S = 0.100     # a silence at least this long splits speech regions
SNAP_S = 0.120             # snap window for word starts (SPEC 4)
MIN_WORD_S = 0.050         # two word starts are never closer than this
MAX_WORD_S = 2.0           # no word lasts longer than this
W_CONST, W_SYL, W_LET, W_FUNC = 0.039, 0.033, 0.047, -0.082   # word duration model (seconds)
W_MIN = 0.05               # shortest expected word
PUNCT_BONUS = 0.15         # extra expected time after , ; : when no pause split the region there
METHOD = "joint"           # "joint": all starts of a region chosen together (default, measured better);
                           # "greedy": each start snapped in turn to the nearest onset (SPEC 4 baseline)
B_ONSET, B_DIP = 0.5, 0.5  # joint solver: reward for a start on an onset peak / after an energy dip
DUR_SIGMA = 0.5            # joint solver: spread of log(duration / expected)


class AlignError(RuntimeError):
    pass


class WordTimingError(LookupError):
    """Raised by a lookup for a line or word that is not in words.json."""


# --------------------------------------------------------------------------- text
_V3 = re.compile(r"\[pause\s+\d+\s*(?:ms)?\]|\[beat\]|\{/?(?:slow|fast)\}", re.I)
_EMPH = re.compile(r"\*([^*\s](?:[^*]*[^*\s])?)\*")
_ENGINE_CMD = re.compile(r"\[\[[^\]]*\]\]")          # e.g. [[slnc 300]], [[emph +]]
_E1_PAIR = re.compile(r"⟦\s*([A-Za-z][\w-]*)\s*⟧(.*?)⟦\s*/\s*\1\s*⟧", re.S)
_E1_ROLE = re.compile(r"⟦\s*[A-Za-z][\w-]*\s*[:|]\s*(.*?)⟧", re.S)
_E1_ANY = re.compile(r"⟦(.*?)⟧", re.S)
_EDGE = re.compile(r"^[^\w]+|[^\w]+$", re.U)


def strip_markup(text: str) -> str:
    """Display text without script markup: V3 pauses/speed/emphasis, colour-role
    caption marks (paired, role:words or bare brackets) and engine commands."""
    s = str(text or "")
    s = _ENGINE_CMD.sub(" ", s)
    s = _V3.sub(" ", s)
    s = _EMPH.sub(r"\1", s)
    for _ in range(3):
        s2 = _E1_PAIR.sub(r"\2", s)
        if s2 == s:
            break
        s = s2
    s = _E1_ROLE.sub(r"\1", s)
    s = _E1_ANY.sub(r"\1", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", s)


def norm_word(w: str) -> str:
    """Edge punctuation removed; inner apostrophes/hyphens/dots kept."""
    return _EDGE.sub("", str(w).replace("_", " ").strip()) if w else ""


def _key(w: str) -> str:
    return norm_word(w).casefold()


def display_tokens(text: str) -> list:
    """[(word, raw_token)] for the display text; tokens without letters/digits dropped."""
    out = []
    for tok in strip_markup(text).split():
        w = norm_word(tok)
        if w and re.search(r"\w", w):
            out.append((w, tok))
    return out


def display_words(text: str) -> list:
    return [w for w, _ in display_tokens(text)]


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_words(n: int) -> str:
    """English words for 0 <= n < 10**9 (for syllable counts and token matching)."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rest == 0 else " " + number_words(rest))
    for div, name in ((10 ** 6, "million"), (1000, "thousand")):
        if n >= div:
            rest = n % div
            return number_words(n // div) + " " + name + ("" if rest == 0 else " " + number_words(rest))
    return str(n)


_LETTER_SYL = {"w": 3}


def syllables(word: str) -> int:
    """Vowel-group heuristic syllable count of ONE spoken token (>= 1)."""
    w = re.sub(r"[^A-Za-z0-9']", "", word or "")
    if not w:
        return 0
    if w.isdigit():
        if len(w) <= 9:
            return sum(syllables(x) for x in number_words(int(w)).split())
        return max(1, round(1.5 * len(w)))
    if re.fullmatch(r"\d+[A-Za-z]+|[A-Za-z]+\d+", w):           # "v2", "3rd"
        parts = re.findall(r"\d+|[A-Za-z]+", w)
        return sum(syllables(p) for p in parts)
    if len(w) == 1:                                                # a spelled letter
        return _LETTER_SYL.get(w.lower(), 1)
    s = w.lower().replace("'", "")
    groups = re.findall(r"[aeiouy]+", s)
    n = len(groups)
    if s.endswith("e") and not s.endswith(("le", "ee", "ye")) and n > 1:
        n -= 1                                                     # silent final e
    if s.endswith("es") and n > 1 and not re.search(r"(ss|sh|ch|x|z|c|g)es$", s):
        n -= 1                                                     # "makes", "lines"
    if s.endswith("ed") and n > 1 and not re.search(r"[td]ed$", s):
        n -= 1                                                     # "checked", "moved"
    if not re.search(r"[aeiouy]", s):                              # "hmm", "pfft", a mixed-case code name
        return max(1, len(s) // 3)
    return max(1, n)


def _spoken_of_display(word: str, spell=None, keep=None) -> list:
    if to_speech is None:
        return word.split()
    return to_speech(word, ssml=False, spell=set(spell or ()), word=set(keep or ())).split()


def _cmp_form(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\d+", lambda m: number_words(int(m.group(0))) if len(m.group(0)) <= 9 else m.group(0), s)
    return re.sub(r"[^a-z0-9]", "", s)


def map_spoken_to_display(display: list, spoken: list, max_merge: int = 8) -> list:
    """Monotonic alignment of spoken tokens onto display words.

    Returns, per display word, a half-open (s0, s1) range of spoken-token
    indexes. Blocks are (1 display, k spoken) or (k display, 1 spoken); cost is
    1 - string similarity of their letter forms (numbers spelled out), so
    "SOP" <- ["S", "O", "P"] and "42" <- ["forty", "two"] map exactly.
    A multi-display block shares its spoken range (each word gets the range;
    the caller splits the time). Raises AlignError if no alignment exists."""
    D, S = len(display), len(spoken)
    dk = [_cmp_form(x) for x in display]
    sk = [_cmp_form(x) for x in spoken]
    INF = float("inf")
    dp = [[INF] * (S + 1) for _ in range(D + 1)]
    back = [[None] * (S + 1) for _ in range(D + 1)]
    dp[0][0] = 0.0
    for i in range(D + 1):
        for j in range(S + 1):
            c0 = dp[i][j]
            if c0 == INF:
                continue
            if j < S and not sk[j]:                       # a spoken token with no letters
                if c0 < dp[i][j + 1]:
                    dp[i][j + 1], back[i][j + 1] = c0, (i, j, "skip")
            moves = [(1, b) for b in range(1, max_merge + 1)] + [(a, 1) for a in range(2, 4)]
            for a, b in moves:
                i2, j2 = i + a, j + b
                if i2 > D or j2 > S:
                    continue
                x, y = "".join(dk[i:i2]), "".join(sk[j:j2])
                sim = difflib.SequenceMatcher(None, x, y, autojunk=False).ratio() if (x or y) else 1.0
                cost = (1.0 - sim) + (0.0 if (a, b) == (1, 1) else 0.04 * (a + b - 2))
                if c0 + cost < dp[i2][j2]:
                    dp[i2][j2], back[i2][j2] = c0 + cost, (i, j, "blk")
    if dp[D][S] == INF:
        raise AlignError(f"cannot map {S} spoken tokens onto {D} display words")
    ranges = [None] * D
    i, j = D, S
    while (i, j) != (0, 0):
        pi, pj, kind = back[i][j]
        if kind == "blk":
            for k in range(pi, i):
                ranges[k] = (pj, j, pi, i)
        i, j = pi, pj
    return ranges


# --------------------------------------------------------------------------- audio
def read_wav(path) -> tuple:
    """-> (float samples in [-1, 1] as a list or numpy array, sample_rate). Mono mix."""
    with wave.open(str(path), "rb") as w:
        sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = array.array("h")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        scale = 32768.0
    elif sw == 1:
        a = array.array("h", [b - 128 for b in raw])
        scale = 128.0
    elif sw == 4:
        a = array.array("i")
        a.frombytes(raw)
        if sys.byteorder == "big":
            a.byteswap()
        scale = 2147483648.0
    else:
        raise AlignError(f"{path}: unsupported {8 * sw}-bit wav")
    if np is not None:
        x = np.asarray(a, dtype=np.float64) / scale
        if ch > 1:
            x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
        return x, sr
    if ch > 1:
        return [sum(a[i:i + ch]) / ch / scale for i in range(0, len(a) - ch + 1, ch)], sr
    return [v / scale for v in a], sr


def _frames_energy_py(x, sr):
    hop, win = max(1, int(round(sr * HOP_S))), max(1, int(round(sr * WIN_S)))
    n = max(1, (len(x) - win) // hop + 1) if len(x) >= win else 1
    sq = [v * v for v in x]
    pre = [0.0]
    acc = 0.0
    for v in sq:
        acc += v
        pre.append(acc)
    db = []
    for k in range(n):
        a = k * hop
        b = min(len(x), a + win)
        e = (pre[b] - pre[a]) / max(1, b - a)
        db.append(10 * math.log10(e + 1e-12))
    return db, hop, win


def analyse(x, sr) -> dict:
    """Frame energy (dB) and an onset-strength curve, both at the 10 ms hop."""
    if np is not None:
        hop, win = max(1, int(round(sr * HOP_S))), max(1, int(round(sr * WIN_S)))
        if len(x) < win:
            x = np.pad(x, (0, win - len(x)))
        n = (len(x) - win) // hop + 1
        idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
        fr = x[idx]
        db = 10 * np.log10((fr ** 2).mean(axis=1) + 1e-12)
        spec = np.abs(np.fft.rfft(fr * np.hanning(win)[None, :], axis=1))
        lo, hi = int(100 * win / sr), max(2, int(4000 * win / sr))
        L = np.log1p(1000.0 * spec[:, lo:hi])
        flux = np.zeros(n)
        flux[1:] = np.maximum(0.0, L[1:] - L[:-1]).sum(axis=1)
        return {"db": db.tolist(), "onset": flux.tolist(), "hop": hop, "win": win, "sr": sr,
                "method": "spectral flux (numpy)"}
    db, hop, win = _frames_energy_py(x, sr)
    sm = [sum(db[max(0, k - 1):k + 2]) / len(db[max(0, k - 1):k + 2]) for k in range(len(db))]
    rise = [0.0] + [max(0.0, sm[k] - sm[k - 1]) for k in range(1, len(sm))]
    return {"db": db, "onset": rise, "hop": hop, "win": win, "sr": sr,
            "method": "energy rise (pure Python)"}


def _t(frame: int, an: dict) -> float:
    """Time of a frame's analysis-window centre."""
    return (frame * an["hop"] + an["win"] / 2.0) / an["sr"]


def speech_regions(an: dict, long_silence: float = LONG_SILENCE_S) -> list:
    """[(t0, t1)] runs of voiced frames separated by silences >= long_silence."""
    db = an["db"]
    if not db:
        return []
    srt = sorted(db)
    peak = srt[int(0.98 * (len(srt) - 1))]
    floor = srt[int(0.05 * (len(srt) - 1))]
    thr = max(peak - 38.0, floor + 0.2 * (peak - floor), -75.0)
    on = [v > thr for v in db]
    hop_s = an["hop"] / an["sr"]
    gap_frames = max(1, int(round(long_silence / hop_s)))
    runs, k, n = [], 0, len(on)
    while k < n:
        if on[k]:
            j = k
            while j < n and on[j]:
                j += 1
            runs.append([k, j])
            k = j
        else:
            k += 1
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < gap_frames:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for a, b in merged:
        if (b - a) * hop_s < 0.04:                       # a click, not speech
            continue
        if max(db[a:b]) < peak - 25.0:                   # a breath or noise burst
            continue
        t0 = max(0.0, (a * an["hop"] + an["win"] * 0.5 - an["hop"]) / an["sr"])
        t1 = ((b - 1) * an["hop"] + an["win"] * 0.5 + an["hop"]) / an["sr"]
        out.append((t0, t1))
    return out


def refine_starts(x, sr, regions: list) -> list:
    """Move each region start to the first sample where a 2 ms amplitude envelope
    exceeds -34 dB of the line's peak (searched -30..+60 ms around the frame
    estimate): frame resolution is 10 ms, an onset is sharper than that."""
    n = len(x)
    if not n or not regions:
        return regions
    w = max(1, int(sr * 0.002))
    if np is not None:
        env = np.convolve(np.abs(np.asarray(x)), np.ones(w) / w, mode="same")
        thr = float(env.max()) * 0.02
    else:
        thr = None
    out = []
    for a, b in regions:
        lo, hi = max(0, int((a - 0.03) * sr)), min(n, int((a + 0.06) * sr))
        t = a
        if np is not None:
            idx = np.nonzero(env[lo:hi] > thr)[0]
            if len(idx):
                t = (lo + int(idx[0])) / sr
        else:
            if thr is None:
                step = max(1, n // 20000)
                pk = 0.0
                acc = 0.0
                for i in range(0, n - w, step):
                    v = sum(abs(x[j]) for j in range(i, i + w)) / w
                    pk = max(pk, v)
                thr = pk * 0.02
            acc = sum(abs(x[j]) for j in range(lo, min(n, lo + w)))
            for i in range(lo, hi - w):
                if acc / w > thr:
                    t = (i + w // 2) / sr
                    break
                acc += abs(x[i + w]) - abs(x[i])
        out.append((min(max(0.0, t), b - 0.02), b))
    return out


def onset_peaks(an: dict, regions: list) -> list:
    """[(t, strength 0..1)] peaks of the onset curve inside speech regions
    (adaptive threshold: local mean over +-100 ms plus a margin)."""
    o = an["onset"]
    n = len(o)
    if not n:
        return []
    ins = [v for k, v in enumerate(o) if any(r0 <= _t(k, an) <= r1 for r0, r1 in regions)]
    ref = sorted(ins)[int(0.95 * (len(ins) - 1))] if ins else max(o)
    ref = ref or 1.0
    on = [min(1.5, v / ref) for v in o]
    pre = [0.0]
    for v in on:
        pre.append(pre[-1] + v)
    peaks = []
    for k in range(1, n - 1):
        v = on[k]
        if v < 0.12:
            continue
        if v < max(on[max(0, k - 3):k + 4]):
            continue
        a, b = max(0, k - 10), min(n, k + 11)
        if v < (pre[b] - pre[a]) / (b - a) + 0.05:
            continue
        t = _t(k, an) - an["hop"] / an["sr"] / 2.0
        if any(r0 - 0.02 <= t <= r1 for r0, r1 in regions):
            peaks.append((t, min(1.0, v)))
    return peaks


# --------------------------------------------------------------------------- distribution
def _letters(tok: str) -> int:
    core = re.sub(r"[^A-Za-z0-9]", "", tok or "")
    if len(core) == 1 and core.isalpha():
        return 3                                   # a spelled letter is said as its name ("ess")
    if core.isdigit() and len(core) <= 9:
        return len(re.sub(r"[^a-z]", "", number_words(int(core))))
    return len(core)


# closed-class English words: short and unstressed in connected speech
FUNCTION_WORDS = frozenset("""a an the of in on at to into onto and or nor but so if as is are was were be been
by for from with without it its it's that this these those than then not no do does did has have had i you he she
we they me him her us them my your his our their who which what when where how can will would should could may
might must shall there here up out off over""".split())


def word_weights(display: list, spoken_for: list) -> list:
    """Expected duration per display word, in seconds at a typical rate: a linear
    model of syllables and letters of the SPOKEN form (fitted on half of the
    reference sentences; the other half is held out for the accuracy figure)."""
    out = []
    for w, sp in zip(display, spoken_for):
        toks = sp or [w]
        syl = sum(syllables(t) for t in toks) or 1
        let = sum(_letters(t) for t in toks) or 1
        fw = W_FUNC if w.casefold() in FUNCTION_WORDS else 0.0
        out.append(max(W_MIN, W_CONST + W_SYL * syl + W_LET * let + fw))
    return out


def _assign_regions(weights: list, punct: list, regions: list) -> list:
    """Contiguous word groups per region, by DP over log duration ratio.
    Returns [(w0, w1, region_index)]."""
    K, R = len(weights), len(regions)
    durs = [max(0.02, b - a) for a, b in regions]
    rate = sum(durs) / max(1e-9, sum(weights))
    pre = [0.0]
    for w in weights:
        pre.append(pre[-1] + w)
    INF = float("inf")
    dp = [[INF] * (K + 1) for _ in range(R + 1)]
    bk = [[None] * (K + 1) for _ in range(R + 1)]
    dp[0][0] = 0.0
    for r in range(R):
        for i in range(K):
            if dp[r][i] == INF:
                continue
            for j in range(i + 1, K - (R - r - 1) + 1):
                exp = (pre[j] - pre[i]) * rate
                c = math.log(durs[r] / exp) ** 2
                if r < R - 1 and not punct[j - 1]:
                    c += 0.35                                  # a pause usually sits at punctuation
                if dp[r][i] + c < dp[r + 1][j]:
                    dp[r + 1][j], bk[r + 1][j] = dp[r][i] + c, i
    groups, j = [], K
    for r in range(R, 0, -1):
        i = bk[r][j]
        groups.append((i, j, r - 1))
        j = i
    return list(reversed(groups))


def _spread(t0: float, t1: float, weights: list) -> list:
    tot = sum(weights) or 1.0
    out, acc = [], 0.0
    for w in weights:
        out.append(t0 + (t1 - t0) * acc / tot)
        acc += w
    return out


def _boundary_scores(an: dict) -> list:
    """Per frame: how much a word boundary looks like it starts here (0..1):
    the normalised onset curve plus an energy dip just before."""
    o, db = an["onset"], an["db"]
    n = len(o)
    if not n:
        return []
    srt = sorted(o)
    ref = srt[int(0.95 * (n - 1))] or (max(o) or 1.0)
    on = [min(1.0, v / ref) for v in o]
    out = []
    for k in range(n):
        a, b = max(0, k - 8), min(n, k + 9)
        loc_max = max(db[a:b])
        pre = min(db[max(0, k - 3):k + 1])
        dip = max(0.0, min(1.0, (loc_max - pre) / 20.0))
        out.append(B_ONSET * on[k] + B_DIP * dip)
    return out


def _viterbi_region(r0: float, r1: float, wts: list, an: dict, scores: list) -> list:
    """Word starts inside one speech region, chosen jointly: each start lies on
    the 10 ms grid; cost = squared log ratio of each word's duration to its
    expected share, minus the boundary score where a word begins."""
    K = len(wts)
    if K <= 1:
        return [r0]
    hop_s = an["hop"] / an["sr"]
    grid = []
    t = r0
    while t < r1 - MIN_WORD_S / 2:
        grid.append(t)
        t += hop_s
    G = len(grid)
    if G < K:
        return _spread(r0, r1, wts)
    rate = (r1 - r0) / max(1e-9, sum(wts))
    exp = [max(MIN_WORD_S, w * rate) for w in wts]

    def score_at(tt):
        k = int(round((tt * an["sr"] - an["win"] / 2.0) / an["hop"] + 0.5))
        return scores[k] if 0 <= k < len(scores) else 0.0
    sc = [score_at(g) for g in grid]
    INF = float("inf")
    # cost[k][g]: best cost with word k starting at grid g
    cost = [[INF] * G for _ in range(K)]
    back = [[-1] * G for _ in range(K)]
    cost[0][0] = 0.0
    inv = 1.0 / (DUR_SIGMA * DUR_SIGMA)
    for k in range(1, K):
        e = exp[k - 1]
        lo = max(MIN_WORD_S, e * 0.25)
        hi = min(MAX_WORD_S, e * 4.0)
        prev = cost[k - 1]
        for g in range(k, G - (K - 1 - k)):
            best, bi = INF, -1
            tg = grid[g]
            for h in range(g - 1, k - 2, -1):
                d = tg - grid[h]
                if d < lo:
                    continue
                if d > hi:
                    break
                c0 = prev[h]
                if c0 == INF:
                    continue
                c = c0 + inv * math.log(d / e) ** 2
                if c < best:
                    best, bi = c, h
            if bi >= 0:
                cost[k][g] = best - sc[g]
                back[k][g] = bi
    last = exp[K - 1]
    best, bg = INF, -1
    for g in range(G):
        c = cost[K - 1][g]
        if c == INF:
            continue
        d = max(1e-3, r1 - grid[g])
        c += inv * math.log(d / last) ** 2
        if c < best:
            best, bg = c, g
    if bg < 0:
        return _spread(r0, r1, wts)
    starts, g = [], bg
    for k in range(K - 1, -1, -1):
        starts.append(grid[g])
        g = back[k][g]
    return list(reversed(starts))


def onset_align(display: list, spoken_for: list, punct: list, x, sr, *,
                long_silence: float | None = None, snap: float | None = None,
                method: str | None = None) -> tuple:
    """Fallback timing for one line's wav. Returns ([(start, end)] per display
    word in seconds inside the wav, diagnostics dict)."""
    long_silence = LONG_SILENCE_S if long_silence is None else long_silence
    snap = SNAP_S if snap is None else snap
    method = METHOD if method is None else method
    K = len(display)
    an = analyse(x, sr)
    dur = (len(x) / sr) if sr else 0.0
    regions = speech_regions(an, long_silence)
    if not regions:
        regions = [(0.0, dur)]
    while len(regions) > max(1, K):                         # more regions than words: join the closest
        gaps = [regions[k + 1][0] - regions[k][1] for k in range(len(regions) - 1)]
        g = gaps.index(min(gaps))
        regions[g:g + 2] = [(regions[g][0], regions[g + 1][1])]
    regions = refine_starts(x, sr, regions)
    peaks = onset_peaks(an, regions)
    weights = word_weights(display, spoken_for)
    wts = [w + (PUNCT_BONUS if p else 0.0) for w, p in zip(weights, punct)]
    groups = _assign_regions(weights, punct, regions)
    spans = [None] * K
    snapped = 0
    scores = _boundary_scores(an) if method == "joint" else None
    for w0, w1, r in groups:
        r0, r1 = regions[r]
        if method == "joint":
            ww = list(wts[w0:w1])
            ww[-1] = weights[w1 - 1]                      # no pause bonus at a region end
            starts = _viterbi_region(r0, r1, ww, an, scores)
            for n, k in enumerate(range(w0, w1)):
                st = starts[n]
                en = starts[n + 1] if n + 1 < len(starts) else r1
                spans[k] = (st, min(en, st + MAX_WORD_S))
            continue
        starts = [r0]
        cur = r0
        for k in range(w0 + 1, w1):
            est = _spread(cur, r1, wts[k - 1:w1])[1]
            lo = cur + MIN_WORD_S
            hi = r1 - MIN_WORD_S * (w1 - k)
            cand = [(abs(t - est) - 0.25 * s * snap, t) for t, s in peaks
                    if abs(t - est) <= snap and lo <= t <= hi]
            if cand:
                t = min(cand)[1]
                snapped += 1
            else:
                t = min(max(est, lo), max(lo, hi))
            starts.append(t)
            cur = t
        for n, k in enumerate(range(w0, w1)):
            st = starts[n]
            en = starts[n + 1] if n + 1 < len(starts) else r1
            spans[k] = (st, min(en, st + MAX_WORD_S))
    diag = {"method": f"{method}; {an['method']}", "regions": [[round(a, 3), round(b, 3)] for a, b in regions],
            "onsets": len(peaks), "snapped_starts": snapped,
            "inner_starts": sum(max(0, w1 - w0 - 1) for w0, w1, _ in groups)}
    return spans, diag


# --------------------------------------------------------------------------- engine timings
def engine_spans(display: list, timings: list) -> list | None:
    """Map engine word timings (render.py `word_timings`: [{word, spoken?, start,
    end}] — display words when the mapping was exact, else spoken words) onto the
    display words. Returns [(start, end)] or None if they cannot be mapped."""
    if not timings:
        return None
    try:
        tw = [norm_word(t.get("word", "")) for t in timings]
        st = [float(t["start"]) for t in timings]
        en = [float(t["end"]) for t in timings]
    except (KeyError, TypeError, ValueError):
        return None
    keep = [k for k, w in enumerate(tw) if w]
    tw, st, en = [tw[k] for k in keep], [st[k] for k in keep], [en[k] for k in keep]
    if [_key(w) for w in tw] == [_key(w) for w in display]:
        return list(zip(st, en))
    # spoken-form words (or a split/merged display word): map tokens -> display words
    # flat token list with per-token times (a multi-token entry splits its span evenly)
    toks, ts, te = [], [], []
    for k in range(len(tw)):
        t = timings[keep[k]]
        parts = [tw[k]]
        sp = t.get("spoken")
        if sp and _key(tw[k]) not in {_key(d) for d in display} and len(str(sp).split()) > 1:
            parts = str(sp).split()
        n = len(parts)
        for m, p in enumerate(parts):
            toks.append(p)
            ts.append(st[k] + (en[k] - st[k]) * m / n)
            te.append(st[k] + (en[k] - st[k]) * (m + 1) / n)
    try:
        ranges = map_spoken_to_display(display, toks)
    except AlignError:
        return None
    out = []
    for k, rg in enumerate(ranges):
        if rg is None:
            return None
        s0, s1, d0, d1 = rg
        if s1 <= s0:
            return None
        a, b = ts[s0], te[s1 - 1]
        if d1 - d0 > 1:                                   # several display words share tokens
            m = k - d0
            a, b = a + (b - a) * m / (d1 - d0), a + (b - a) * (m + 1) / (d1 - d0)
        out.append((a, b))
    return out


def silence_correct(spans: list, regions: list) -> tuple:
    """Engine timings checked against the audio: no word starts or ends inside a
    long silence (the engine sometimes books a pause onto a neighbouring
    phoneme). A start inside a silence moves to its end; an end inside a silence
    moves to its start. A word with speech on both sides is left alone.
    Returns (spans, number of words changed)."""
    gaps = [(regions[k][1], regions[k + 1][0]) for k in range(len(regions) - 1)]
    out, n = [], 0
    for a, b in spans:
        a2, b2 = a, b
        for g0, g1 in gaps:
            if g0 + 1e-3 < a2 < g1:
                a2 = g1
            if g0 < b2 < g1 - 1e-3 and a2 < g0:
                b2 = g0
        b2 = max(a2, b2)
        if abs(a2 - a) > 1e-3 or abs(b2 - b) > 1e-3:
            n += 1
        out.append((a2, b2))
    return out, n


def implausible_words(display: list, spoken_for: list, spans: list, hi: float = 3.0,
                      lo: float = 0.2) -> list:
    """Words whose timed length (start to end) is far outside the duration
    model at the line's own rate — a sign the timing source lost track (reported,
    never silently changed)."""
    if len(spans) < 3:
        return []
    w = word_weights(display, spoken_for)
    d = [max(0.0, b - a) for a, b in spans]
    rate = sum(d) / max(1e-9, sum(w))
    out = []
    for k, dk in enumerate(d):
        exp = w[k] * rate
        if exp > 0 and (dk / exp > hi or dk / exp < lo):
            out.append({"i": k, "w": display[k], "seconds": round(dk, 3), "expected": round(exp, 3)})
    return out


# --------------------------------------------------------------------------- per line
def line_name(s: dict, i: int) -> str:
    return str(s.get("name") or s.get("id") or f"s{s.get('idx', i + 1)}")


def spoken_for_display(display: list, speech: str | None, spell=None, keep=None) -> list:
    """Spoken tokens per display word (from `speech` when given, else derived)."""
    if speech:
        toks = strip_markup(speech).split()
        try:
            ranges = map_spoken_to_display(display, toks)
            res = []
            for rg in ranges:
                if rg is None:
                    res.append([])
                    continue
                s0, s1, d0, d1 = rg
                part = toks[s0:s1]
                res.append(part if d1 - d0 == 1 else part[:max(1, len(part) // (d1 - d0))])
            return res
        except AlignError:
            pass
    return [_spoken_of_display(w, spell, keep) for w in display]


def align_line(s: dict, i: int, *, wav=None, timings=None, prefer: str = "engine",
               spell=None, keep=None, check_silence: bool = True, method: str | None = None) -> dict:
    """Time one beat-map line. Returns the words.json line plus `_source`/`_diag`."""
    text = strip_markup(s.get("text", ""))
    toks = display_tokens(text)
    display = [w for w, _ in toks]
    punct = [bool(re.search(r"[,;:.!?…—–]$", raw)) for _, raw in toks]
    start = float(s.get("start", 0.0))
    end = float(s.get("end", start))
    off = float(s.get("audio_start", start))
    spans, source, diag = None, None, {}
    if not display:                                       # punctuation only: nothing to time
        return {"name": line_name(s, i), "text": text, "start": round(start, 3), "end": round(end, 3),
                "words": [], "_source": "engine" if timings else "onset", "_diag": {}}
    if prefer != "onset" and timings:
        spans = engine_spans(display, timings)
        if spans is not None:
            source = "engine"
            if wav is not None and Path(wav).exists() and check_silence:
                x, sr = read_wav(wav)
                an = analyse(x, sr)
                regs = refine_starts(x, sr, speech_regions(an))
                spans, nfix = silence_correct(spans, regs)
                diag["silence_corrected_words"] = nfix
            sp = spoken_for_display(display, s.get("speech"), spell, keep)
            odd = implausible_words(display, sp, spans)
            if odd:
                diag["implausible_engine_words"] = odd
    if spans is None:
        if wav is None or not Path(wav).exists():
            why = "no engine word timings" if not timings else "engine word timings could not be mapped"
            raise AlignError(f"line {line_name(s, i)!r}: {why} and no audio found for the fallback")
        x, sr = read_wav(wav)
        sp = spoken_for_display(display, s.get("speech"), spell, keep)
        spans, diag = onset_align(display, sp, punct, x, sr, method=method)
        source = "onset"
    words, prev = [], start
    clamped = 0
    for k, (w, (a, b)) in enumerate(zip(display, spans)):
        a, b = a + off, b + off
        a2 = min(max(a, start, prev), end)
        b2 = min(max(b, a2), end, a2 + MAX_WORD_S)
        if abs(a2 - a) > 0.001 or abs(b2 - b) > 0.001:
            clamped += 1
        words.append({"w": w, "start": round(a2, 3), "end": round(b2, 3), "i": k})
        prev = a2
    if clamped:
        diag["clamped_to_line_window"] = clamped
    return {"name": line_name(s, i), "text": text, "start": round(start, 3), "end": round(end, 3),
            "words": words, "_source": source, "_diag": diag}


def _find_wav(s: dict, i: int, audio_dir, base: Path | None):
    cands = []
    if s.get("wav"):
        p = Path(s["wav"])
        cands += [p] if p.is_absolute() else [Path(audio_dir or ".") / p, (base or Path(".")) / p]
    if audio_dir:
        d = Path(audio_dir)
        # name / id first; then render.py's default name for the i-th line (0-based)
        for stem in (s.get("name"), s.get("id"), f"line{i:03d}"):
            if stem:
                cands.append(d / f"{stem}.wav")
    return next((c for c in cands if c.exists()), None)


def _results_index(results) -> tuple:
    if isinstance(results, dict):
        results = results.get("results") or results.get("lines") or []
    by_stem = {}
    for r in results or []:
        if r.get("out_wav"):
            by_stem[Path(r["out_wav"]).stem] = r
    return list(results or []), by_stem


def align_beatmap(beatmap: dict, audio_dir=None, *, results=None, prefer: str = "engine",
                  base: Path | None = None, spell=None, keep=None, method: str | None = None) -> tuple:
    """-> (words dict in the words.json format, report dict)."""
    sents = list(beatmap.get("sentences") or beatmap.get("lines") or [])
    rlist, rstem = _results_index(results)
    lines, rep, errors = [], [], []
    for i, s in enumerate(sents):
        if not strip_markup(s.get("text", "")):
            continue
        r = None
        for stem in (s.get("name"), s.get("id")):
            if stem and stem in rstem:
                r = rstem[stem]
        if r is None and rlist and len(rlist) == len(sents):
            r = rlist[i]
        timings = s.get("word_timings") or (r or {}).get("word_timings")
        wav = _find_wav(s, i, audio_dir, base)
        if wav is None and r and r.get("out_wav") and Path(r["out_wav"]).exists():
            wav = Path(r["out_wav"])
        try:
            ln = align_line(s, i, wav=wav, timings=timings, prefer=prefer, spell=spell, keep=keep,
                            method=method)
        except AlignError as ex:
            errors.append(str(ex))
            continue
        rep.append({"name": ln["name"], "source": ln.pop("_source"), **ln.pop("_diag")})
        lines.append(ln)
    srcs = {r["source"] for r in rep}
    source = "engine" if srcs == {"engine"} else ("onset" if srcs == {"onset"} else "mixed")
    words = {"lines": lines, "source": source, "fps": int(beatmap.get("fps") or 30)}
    return words, {"lines": rep, "errors": errors, "numpy": np is not None}


def to_js(words: dict) -> str:
    return "window.__C8_WORDS__ = " + json.dumps(words, ensure_ascii=False, separators=(",", ":")) + ";\n"


# --------------------------------------------------------------------------- consumer
class Words:
    """Python mirror of the scene runtime's atLine/atWord: a miss THROWS."""

    def __init__(self, data: dict):
        self.data = data
        self.fps = int(data.get("fps") or 30)
        self.lines = {ln["name"]: ln for ln in data.get("lines", [])}

    @classmethod
    def load(cls, path) -> "Words":
        txt = Path(path).read_text(encoding="utf-8").strip()
        if txt.startswith("window.__C8_WORDS__"):
            txt = txt.split("=", 1)[1].strip().rstrip(";")
        return cls(json.loads(txt))

    def line(self, name: str) -> dict:
        if name not in self.lines:
            raise WordTimingError(f"line {name!r} is not in words.json (have: {sorted(self.lines)[:12]})")
        return self.lines[name]

    def at_line(self, name: str) -> float:
        return float(self.line(name)["start"])

    def word(self, name: str, word: str, nth: int = 0) -> dict:
        ln = self.line(name)
        want = [_key(p) for p in str(word).split() if _key(p)]
        if not want:
            raise WordTimingError(f"empty word for line {name!r}")
        ws = ln["words"]
        keys = [_key(w["w"]) for w in ws]
        hits = [k for k in range(len(ws) - len(want) + 1) if keys[k:k + len(want)] == want]
        if len(hits) <= nth:
            raise WordTimingError(f"{word!r} (occurrence {nth}) is not in line {name!r}: {ln.get('text')!r}")
        return ws[hits[nth]]

    def at_word(self, name: str, word: str, nth: int = 0) -> float:
        return float(self.word(name, word, nth)["start"])

    def at_word_frame(self, name: str, word: str, nth: int = 0) -> int:
        return int(round(self.at_word(name, word, nth) * self.fps))


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Word-level timing for narration lines (local only).")
    ap.add_argument("beatmap")
    ap.add_argument("--audio-dir", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--results", default=None, help="voice render results JSON (may carry word_timings)")
    ap.add_argument("--prefer", choices=["engine", "onset"], default="engine")
    ap.add_argument("--js", nargs="?", const="auto", default=None,
                    help="also write window.__C8_WORDS__ = ...; (to PATH, default <out>.js)")
    ap.add_argument("--spell", default="", help="comma list: tokens the voice spelled")
    ap.add_argument("--word", default="", help="comma list: tokens the voice said as words")
    ap.add_argument("--method", choices=["joint", "greedy"], default=None,
                    help="fallback solver: joint (default) or greedy nearest-onset snapping")
    ap.add_argument("--report", default=None, help="write per-line diagnostics JSON here")
    a = ap.parse_args(argv)
    bm_path = Path(a.beatmap)
    try:
        bm = json.loads(bm_path.read_text(encoding="utf-8"))
        res = json.loads(Path(a.results).read_text(encoding="utf-8")) if a.results else None
    except (OSError, ValueError) as ex:
        sys.stderr.write(f"[coordn8:align] cannot read input: {ex}\n")
        return 3
    spell = {x.strip() for x in a.spell.split(",") if x.strip()}
    keep = {x.strip() for x in a.word.split(",") if x.strip()}
    words, rep = align_beatmap(bm, a.audio_dir, results=res, prefer=a.prefer, base=bm_path.parent,
                               spell=spell, keep=keep, method=a.method)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(words, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if a.js:
        jp = out.with_suffix(".js") if a.js == "auto" else Path(a.js)
        jp.write_text(to_js(words), encoding="utf-8")
    if a.report:
        Path(a.report).write_text(json.dumps(rep, indent=1) + "\n", encoding="utf-8")
    n = sum(len(ln["words"]) for ln in words["lines"])
    sys.stderr.write(f"[coordn8:align] {len(words['lines'])} line(s), {n} word(s), source={words['source']}"
                     f"{'; ERRORS: ' + ' | '.join(rep['errors']) if rep['errors'] else ''} -> {out}\n")
    return 3 if rep["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
