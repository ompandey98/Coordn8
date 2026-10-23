#!/usr/bin/env python3
"""Validator and writer for <build>/divergence.json — the divergence step (H2).

For every PIVOTAL decision (the look, the narrative spine, each hero figure, each pivotal
scene) the idea-generator agent writes at least three options, each made with a DIFFERENT
named technique, from a recorded seed; a SEPARATE idea-critic agent judges every option
against three rejection tests; one surviving option is chosen, with the reason.

What this gate judges (outcomes and honesty of the record, never catalog membership):

  options      ≥ 3, each with an id, an idea and a technique; techniques pairwise different
               (an invented technique "invented:<name>" must carry a description); no two
               ideas near-identical (token Jaccard ≥ 0.6 → "not genuinely different")
  randomness   a recorded integer seed and draws that REPLAY from it (scripts/design/seed.py);
               hand-written "random" draws that do not replay are flagged
  critic       generator_agent and critic_agent present and DIFFERENT; every option judged;
               a reject names its test (user-could-think-of-it | known-default |
               surprising-not-useful | other:<name>) and why
  the bar      at least one option survives — otherwise ANOTHER GENERATION ROUND is demanded
               (the gate fails; it never passes a weakened set); an idea rejected in an
               earlier round may not come back as kept ("bar lowered")
  the choice   `chosen` is an option the critic did not reject, with a `why`
  coverage     a decision exists for the look, the narrative spine and every entry of the
               top-level "pivotal" list (and every --require)

The technique catalog (data/idea_techniques.json) is NOT read by this gate: an option's
technique is never checked against it. Only seed replay touches the catalog, and a missing
catalog skips the replay with a note rather than failing anything.

CLI (advisory gate; exit codes per scripts/lib/common.py: 0 PASS, 1 REVIEW, 2 SKIPPED, 3 ERROR):
    divergence.py check <build_dir | divergence.json> [--require KIND[:SUBJECT]]... [--json]
    divergence.py add   <build_dir> <decision.json>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

SCHEMA = 1
FILENAME = "divergence.json"
MIN_OPTIONS = 3
JACCARD_LIMIT = 0.6
CRITIC_TESTS = ("user-could-think-of-it", "known-default", "surprising-not-useful")
VERDICTS = ("keep", "reject")
DEFAULT_REQUIRED = ("look", "narrative-spine")
INVENTED_PREFIX = "invented:"
MIN_DESCRIPTION_WORDS = 4
BLOCKING = {"same-agent", "agents-missing", "no-survivor", "chosen-rejected", "bar-lowered",
            "too-few-options", "technique-repeated", "near-duplicate", "missing-pivotal"}

_STOP = set("""a an the and or but of to in on at by for with from into onto as is are was were be
been being it its this that these those then than so such each every one two three its their
there here which who what when where how all any some no not only also just very more most
we you they he she i our your his her them us me my""".split())

_KIND_RULES = (
    ("narrative-spine", re.compile(r"\b(spine|narrative|story ?arc|arc of the)\b", re.I)),
    ("look", re.compile(r"\b(look|palette|visual system|design system|style kit|kit)\b", re.I)),
    ("hero-figure", re.compile(r"\b(hero|figure|diagram)\b", re.I)),
    ("pivotal-scene", re.compile(r"\b(scene|opening|payoff|hook|closing)\b", re.I)),
)


# ------------------------------------------------------------------ helpers
def tokens(text) -> set:
    words = re.findall(r"[a-z0-9]+", str(text or "").lower())
    return {w for w in words if len(w) > 1 and w not in _STOP}


def jaccard(a, b) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta and not tb:
        return 1.0
    return len(ta & tb) / float(len(ta | tb))


def norm_technique(t) -> str:
    s = str(t or "").strip().lower()
    if s.startswith(INVENTED_PREFIX):
        s = s[len(INVENTED_PREFIX):]
    return re.sub(r"[\s_]+", "-", s.strip()).strip("-")


def is_invented(option: dict) -> bool:
    return (str(option.get("technique") or "").strip().lower().startswith(INVENTED_PREFIX)
            or option.get("invented") is True)


def kind_of(decision: dict) -> str:
    k = decision.get("kind")
    if k:
        return str(k).strip().lower()
    what = str(decision.get("what") or "")
    for kind, rx in _KIND_RULES:
        if rx.search(what):
            return kind
    return re.sub(r"\s+", "-", what.strip().lower()) or "unnamed"


def subject_of(decision: dict):
    s = decision.get("subject")
    return str(s).strip().lower() if s not in (None, "") else None


def label(decision: dict) -> str:
    s = subject_of(decision)
    return f"{kind_of(decision)}{':' + s if s else ''} ({decision.get('what') or 'no what'})"


def _finding(code: str, where: str, detail: str) -> dict:
    return {"code": code, "decision": where, "detail": detail, "blocking": code in BLOCKING}


def _verdict(entry: dict) -> str:
    return str(entry.get("verdict") or "").strip().lower()


def _replay(decision: dict):
    """(problems, note). Replays recorded draws through seed.py when the catalog is present."""
    try:
        from design import seed as seedmod
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location("c8_seed", Path(__file__).with_name("seed.py"))
        seedmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seedmod)
    try:
        catalog = seedmod.load_catalog()
    except (OSError, ValueError):
        return [], "seed replay skipped: technique catalog not available here"
    want = decision.get("catalog_hash")
    if want and want != seedmod.catalog_hash(catalog):
        return [], "seed replay skipped: the catalog changed since these draws were made"
    return seedmod.replay(decision["seed"], decision["draws"], catalog), ""


# ------------------------------------------------------------------ one decision
def check_decision(dec: dict, top: dict | None = None) -> tuple:
    """(findings, notes) for one decision. `top` supplies file-level generator/critic agents."""
    top = top or {}
    where = label(dec)
    f: list = []
    notes: list = []

    # agents — the generator never judges its own options
    gen = str(dec.get("generator_agent") or top.get("generator_agent") or "").strip()
    cri = str(dec.get("critic_agent") or top.get("critic_agent") or "").strip()
    if not gen or not cri:
        f.append(_finding("agents-missing", where, "generator_agent and critic_agent must both be named"))
    elif gen.lower() == cri.lower():
        f.append(_finding("same-agent", where,
                          f"generator_agent and critic_agent are both {gen!r}: the critic must be a separate agent"))

    # options
    options = [o for o in (dec.get("options") or []) if isinstance(o, dict)]
    if len(options) < MIN_OPTIONS:
        f.append(_finding("too-few-options", where,
                          f"{len(options)} option(s); at least {MIN_OPTIONS} genuinely different ones are required"))
    ids = []
    for o in options:
        oid = str(o.get("id") or "").strip()
        if not oid:
            f.append(_finding("option-id-missing", where, "an option has no id"))
        elif oid in ids:
            f.append(_finding("option-id-duplicate", where, f"option id {oid!r} appears twice"))
        ids.append(oid)
        if len(tokens(o.get("idea"))) < 3:
            f.append(_finding("option-idea-empty", where, f"option {oid!r}: the idea is missing or too thin to judge"))
        if not norm_technique(o.get("technique")):
            f.append(_finding("technique-missing", where, f"option {oid!r} names no technique"))
        elif is_invented(o):
            desc = o.get("technique_description") or o.get("description") or ""
            if len(tokens(desc)) < MIN_DESCRIPTION_WORDS:
                f.append(_finding("invented-technique-undescribed", where,
                                  f"option {oid!r}: invented technique {o.get('technique')!r} needs a one-sentence "
                                  "description of how it works"))

    # techniques pairwise different (catalog membership is never consulted)
    seen: dict = {}
    for o in options:
        t = norm_technique(o.get("technique"))
        if not t:
            continue
        if t in seen:
            f.append(_finding("technique-repeated", where,
                              f"options {seen[t]!r} and {o.get('id')!r} both use technique {t!r}; each option needs a "
                              "different technique"))
        else:
            seen[t] = o.get("id")
    listed = dec.get("techniques")
    if isinstance(listed, list) and listed:
        lset = {norm_technique(x) for x in listed}
        for o in options:
            t = norm_technique(o.get("technique"))
            if t and t not in lset:
                f.append(_finding("technique-not-recorded", where,
                                  f"option {o.get('id')!r} uses {t!r}, which the decision's techniques list omits"))

    # near-duplicates (mechanical: token Jaccard)
    for i in range(len(options)):
        for j in range(i + 1, len(options)):
            jac = jaccard(options[i].get("idea"), options[j].get("idea"))
            if jac >= JACCARD_LIMIT:
                f.append(_finding("near-duplicate", where,
                                  f"options {options[i].get('id')!r} and {options[j].get('id')!r} are not genuinely "
                                  f"different (token Jaccard {jac:.2f} ≥ {JACCARD_LIMIT})"))

    # seed + draws (real randomness, replayable)
    seed = dec.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        f.append(_finding("no-seed", where, "no integer seed recorded"))
    draws = dec.get("draws")
    if not isinstance(draws, list) or not draws:
        f.append(_finding("no-draws", where, "no recorded draws (use scripts/design/seed.py and store Draw.record())"))
    elif isinstance(seed, int) and not isinstance(seed, bool):
        bad, note = _replay(dec)
        if note:
            notes.append(f"{where}: {note}")
        if bad:
            f.append(_finding("draws-do-not-replay", where,
                              "recorded draws do not come from the recorded seed: " + "; ".join(bad[:3])))

    # critic
    critic = [c for c in (dec.get("critic") or []) if isinstance(c, dict)]
    if not critic:
        f.append(_finding("critic-missing", where, "no critic entries"))
    verdict: dict = {}
    for c in critic:
        cid = str(c.get("id") or "").strip()
        v = _verdict(c)
        if cid not in ids:
            f.append(_finding("critic-unknown-option", where, f"critic judged {cid!r}, which is not an option"))
            continue
        if v not in VERDICTS:
            f.append(_finding("bad-verdict", where, f"critic verdict for {cid!r} is {c.get('verdict')!r}; use keep|reject"))
            continue
        verdict[cid] = v
        test = str(c.get("test") or "").strip().lower()
        if v == "reject":
            if not test:
                f.append(_finding("reject-without-test", where, f"rejection of {cid!r} names no test"))
            elif test not in CRITIC_TESTS and not test.startswith("other:"):
                f.append(_finding("unknown-test", where,
                                  f"rejection of {cid!r} uses test {test!r}; use one of {', '.join(CRITIC_TESTS)} "
                                  "or other:<name>"))
        if len(tokens(c.get("why"))) < 3:
            f.append(_finding("critic-why-missing", where, f"critic entry for {cid!r} gives no real reason"))
    for oid in ids:
        if oid and oid not in verdict and critic:
            f.append(_finding("option-unjudged", where, f"the critic never judged option {oid!r}"))

    survivors = [oid for oid in ids if oid and verdict.get(oid) == "keep"]
    if options and critic and not survivors:
        f.append(_finding("no-survivor", where,
                          "the critic rejected every option: run ANOTHER GENERATION ROUND with techniques not yet "
                          "used — the bar is not lowered to keep a rejected option"))

    # never lower the bar across rounds
    rounds = [r for r in (dec.get("rounds") or []) if isinstance(r, dict)]
    rejected_before = []
    for r in rounds:
        rv = {str(c.get("id")): _verdict(c) for c in (r.get("critic") or []) if isinstance(c, dict)}
        for o in r.get("options") or []:
            if isinstance(o, dict) and rv.get(str(o.get("id"))) == "reject":
                rejected_before.append((r.get("round"), o.get("id"), o.get("idea")))
    for o in options:
        if verdict.get(str(o.get("id"))) != "keep":
            continue
        for rnd, rid, ridea in rejected_before:
            jac = jaccard(o.get("idea"), ridea)
            if jac >= JACCARD_LIMIT:
                f.append(_finding("bar-lowered", where,
                                  f"kept option {o.get('id')!r} re-admits {rid!r}, rejected in round {rnd} "
                                  f"(token Jaccard {jac:.2f}); a rejected idea may not come back"))

    # the choice
    chosen = str(dec.get("chosen") or "").strip()
    if not chosen:
        f.append(_finding("chosen-missing", where, "no chosen option"))
    elif chosen not in ids:
        f.append(_finding("chosen-unknown", where, f"chosen {chosen!r} is not one of the options"))
    elif verdict.get(chosen) == "reject":
        f.append(_finding("chosen-rejected", where, f"chosen {chosen!r} was rejected by the critic"))
    if len(tokens(dec.get("why"))) < 3:
        f.append(_finding("chosen-why-missing", where, "no reason given for the choice"))
    return f, notes


# ------------------------------------------------------------------ whole file
def _parse_require(items) -> list:
    out = []
    for r in items or []:
        if isinstance(r, dict):
            out.append((str(r.get("kind") or "").strip().lower(), subject_of(r)))
        else:
            k, _, s = str(r).partition(":")
            out.append((k.strip().lower(), s.strip().lower() or None))
    return [x for x in out if x[0]]


def check(doc, required=()) -> dict:
    """Validate a divergence document. Returns {status, summary, findings, notes, needs_round}."""
    findings: list = []
    notes: list = []
    if not isinstance(doc, dict):
        return {"gate": "divergence", "status": "ERROR", "summary": "divergence.json is not an object",
                "findings": [], "notes": [], "needs_round": []}
    if doc.get("schema") != SCHEMA:
        findings.append(_finding("schema", "file", f"schema is {doc.get('schema')!r}; expected {SCHEMA}"))
    decisions = [d for d in (doc.get("decisions") or []) if isinstance(d, dict)]
    if not decisions:
        findings.append(_finding("no-decisions", "file", "no decisions recorded"))
    for d in decisions:
        f, n = check_decision(d, doc)
        findings += f
        notes += n
    need = [(k, None) for k in DEFAULT_REQUIRED] + _parse_require(doc.get("pivotal")) + _parse_require(required)
    have = [(kind_of(d), subject_of(d)) for d in decisions]
    for k, s in dict.fromkeys(need):
        ok = any(hk == k and (s is None or hs == s) for hk, hs in have)
        if not ok:
            findings.append(_finding("missing-pivotal", f"{k}{':' + s if s else ''}",
                                     f"no divergence decision for the pivotal {k}{' ' + s if s else ''}"))
    needs_round = sorted({x["decision"] for x in findings if x["code"] == "no-survivor"})
    status = "PASS" if not findings else "REVIEW"
    blocking = sum(1 for x in findings if x["blocking"])
    summary = (f"{len(decisions)} pivotal decision(s) validated" if status == "PASS" else
               f"{len(findings)} finding(s), {blocking} blocking, across {len(decisions)} decision(s)"
               + (f"; another generation round needed for: {', '.join(needs_round)}" if needs_round else ""))
    return {"gate": "divergence", "status": status, "summary": summary, "findings": findings,
            "notes": notes, "needs_round": needs_round, "decisions": len(decisions)}


def _target(path: Path) -> Path:
    path = Path(path)
    return path if path.suffix == ".json" and not path.is_dir() else path / FILENAME


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".divergence-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def add(build_dir, decision: dict) -> dict:
    """Validate one decision and, only if it is clean, merge it into <build>/divergence.json
    (replacing an earlier decision with the same kind+subject). Never writes an invalid one."""
    target = _target(Path(build_dir))
    doc = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"schema": SCHEMA, "decisions": []}
    if not isinstance(doc, dict):
        raise ValueError(f"{target} is not a JSON object")
    doc.setdefault("schema", SCHEMA)
    doc.setdefault("decisions", [])
    dec = dict(decision)
    if not dec.get("techniques"):
        dec["techniques"] = [o.get("technique") for o in dec.get("options") or [] if isinstance(o, dict)]
    for k in ("generator_agent", "critic_agent"):
        if dec.get(k) and not doc.get(k):
            doc[k] = dec[k]
    findings, notes = check_decision(dec, doc)
    if findings:
        return {"gate": "divergence", "status": "REVIEW", "written": False, "path": str(target),
                "summary": f"decision not written: {len(findings)} finding(s)", "findings": findings, "notes": notes}
    key = (kind_of(dec), subject_of(dec))
    kept = [d for d in doc["decisions"] if (kind_of(d), subject_of(d)) != key]
    replaced = len(kept) != len(doc["decisions"])
    doc["decisions"] = kept + [dec]
    _atomic_write(target, doc)
    return {"gate": "divergence", "status": "PASS", "written": True, "replaced": replaced, "path": str(target),
            "summary": f"{'replaced' if replaced else 'added'} {label(dec)}", "findings": [], "notes": notes}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="divergence.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("build")
    p.add_argument("--require", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("build")
    p.add_argument("decision")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "check":
            target = _target(Path(a.build))
            if not target.exists():
                emit({"gate": "divergence", "status": "SKIPPED", "path": str(target),
                      "summary": f"no {FILENAME} at {target} — the divergence step has not run for this build"})
                return EXIT_SKIPPED
            res = check(json.loads(target.read_text(encoding="utf-8")), a.require)
            res["path"] = str(target)
            emit(res)
            return {"PASS": EXIT_PASS, "REVIEW": EXIT_REVIEW}.get(res["status"], EXIT_ERROR)
        res = add(Path(a.build), json.loads(Path(a.decision).read_text(encoding="utf-8")))
        emit(res)
        return EXIT_PASS if res["written"] else EXIT_REVIEW
    except (OSError, ValueError, json.JSONDecodeError) as ex:
        emit({"gate": "divergence", "status": "ERROR", "summary": f"{type(ex).__name__}: {ex}"})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
