#!/usr/bin/env python3
"""D1 plain-language checker, D2 one term per concept, D3 truth tests — and the regression proof that
adding them changes no existing gate result."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts" / "gates"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_language as cl  # noqa: E402
import check_terms as ct  # noqa: E402
import check_truth as ctr  # noqa: E402

FX = ROOT / "tests" / "v2" / "fixtures" / "language"
OWN = ["scripts/gates/check_language.py", "scripts/gates/check_terms.py", "scripts/gates/check_truth.py",
       "data/language_rules.json"]
s = Suite("language")
RULES = cl.load_rules()


def sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def out_json(r):
    try:
        return json.loads(r.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return {}


def rules_hit(text: str, profile: str) -> list[str]:
    rep = cl.check_blocks([{"text": text, "profile": profile, "where": "t"}], RULES, tagger_mode="heuristic")
    return [f["rule"] for f in rep["findings"]]


with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)

    # ======================================================================== D1 per-rule fixtures
    cases = json.loads((FX / "rule_cases.json").read_text(encoding="utf-8"))["cases"]
    for c in cases:
        bad_pass = [t for t in c["pass"] if c["rule"] in rules_hit(t, c["profile"])]
        missed = [t for t in c["fail"] if c["rule"] not in rules_hit(t, c["profile"])]
        s.check(f"{c['rule']} ({c['profile']}): positive fixture passes", not bad_pass, bad_pass)
        s.check(f"{c['rule']} ({c['profile']}): negative fixture caught", not missed, missed)

    # counting rules (controlled-english-09)
    sent = "Set the read-only flag (the one in the table below)."
    s.check("counting: a hyphenated word counts as one word and a parenthesis counts as one word",
            cl.count_words(sent) == 5 and len(sent.split()) == 10, cl.count_words(sent))
    s.check("counting: a code span and a [[path:line]] chip count as one word each",
            cl.count_words("Run `make all targets` then read [[cache.py:42]].") == 5,
            cl.count_words("Run `make all targets` then read [[cache.py:42]]."))
    w20 = "Set the read-only flag (the one in the table below) on the cache file before the long export job starts tonight at nine."
    s.check("counting: a step that is 20 words only by the counting rules passes the 20-word limit",
            cl.count_words(w20) <= 20 and "length" not in rules_hit(w20, "procedure") and len(w20.split()) > 20,
            (cl.count_words(w20), len(w20.split())))

    # acceptance: 31-word on-screen step fails (CLI, procedure from a numbered list)
    step31 = " ".join(["Open the settings file in the editor and then find the single line near the top that",
                       "sets the size limit for the cache before you save it again carefully now"])
    nwords = len(step31.split())
    md = tmp / "steps.md"
    md.write_text(f"1. {step31}.\n2. Restart the worker.\n", encoding="utf-8")
    r = run("scripts/gates/check_language.py", [md], env=env)
    o = out_json(r)
    lens = [f for f in o.get("findings", []) if f["rule"] == "length"]
    s.check("31-word on-screen step fails the procedure length rule (exit 1, words=31)",
            nwords == 31 and r.returncode == 1 and lens and lens[0].get("words") == 31 and
            lens[0]["profile"] == "procedure", (nwords, r.returncode, lens))
    s.check("tagger is reported (heuristic when no local tagger is installed)",
            o.get("tagger") in ("heuristic", "nltk"), o.get("tagger"))
    r2 = run("scripts/gates/check_language.py", [md, "--tagger", "heuristic"], env=env)
    s.check("--tagger heuristic forces and reports the heuristic path", out_json(r2).get("tagger") == "heuristic",
            r2.stdout[:200])

    # ======================================================================== narration: report only
    nb = FX / "narration_beatmap.json"
    h0 = sha(nb)
    r = run("scripts/gates/check_language.py", [nb], env=env)
    o = out_json(r)
    s.check("narration: \"You'll set up the cache.\" passes (contractions and phrasal verbs allowed, exit 0, "
            "no findings, no notes)", r.returncode == 0 and not o.get("findings") and not o.get("notes"),
            r.stdout[:400])
    s.check("narration: input is byte-identical after the check", sha(nb) == h0 and o.get("inputs_unchanged") is True)
    rough = FX / "narration_rough.json"
    h1 = sha(rough)
    r = run("scripts/gates/check_language.py", [rough], env=env)
    o = out_json(r)
    note_rules = {n["rule"] for n in o.get("notes", [])}
    s.check("narration never fails: a long, passive, multi-idea voice line gives exit 0 with notes only",
            r.returncode == 0 and o.get("status") == "PASS" and not o.get("findings") and
            {"length", "active_voice", "one_idea"} <= note_rules, (r.returncode, note_rules))
    s.check("narration: semicolons and Latin abbreviations are not judged in speech",
            not ({"no_semicolon", "no_latin_abbreviation"} & note_rules), note_rules)
    s.check("narration: one term per concept reported as a note (worker vs processor in the voice)",
            any(n["rule"] == "one_term" and "processor" in n["message"] for n in o.get("notes", [])), note_rules)
    s.check("narration: rough input is byte-identical after the check", sha(rough) == h1 and o.get("inputs_unchanged"))
    r = run("scripts/gates/check_language.py", [rough, "--glossary", FX / "page_content.json", "--aliases",
                                               FX / "aliases.json"], env=env)
    s.check("narration with glossary + alias list: still exit 0 (notes only)",
            r.returncode == 0 and any(n["rule"] == "one_term" for n in out_json(r).get("notes", [])), r.stdout[:300])
    forced = tmp / "desc.md"
    forced.write_text("The cache is small; it is e.g. filled by the worker, and it is read, and it is cleared.\n")
    r = run("scripts/gates/check_language.py", [forced, "--profile", "narration"], env=env)
    s.check("--profile narration on any text: never exit 1", r.returncode == 0 and not out_json(r).get("findings"),
            r.stdout[:300])
    s.check("narration contract stated in the report (report-only, never rewritten)",
            "never rewritten" in out_json(r).get("narration_policy", ""))

    # ======================================================================== input shapes
    pc = FX / "page_content.json"
    r = run("scripts/gates/check_language.py", [pc], env=env)
    o = out_json(r)
    rules_by_prof = {(f["profile"], f["rule"]) for f in o.get("findings", [])}
    s.check("page content JSON: steps component -> procedure profile (imperative caught)",
            ("procedure", "imperative") in rules_by_prof, rules_by_prof)
    s.check("page content JSON: warning callout -> warning profile (missing risk caught)",
            ("warning", "states_risk") in rules_by_prof and o.get("profiles_used", {}).get("warning") == 1,
            rules_by_prof)
    s.check("page content JSON: prose -> description profile (semicolon caught)",
            ("description", "no_semicolon") in rules_by_prof, rules_by_prof)
    r = run("scripts/gates/check_language.py", [FX / "page.html"], env=env)
    o = out_json(r)
    got = {(f["profile"], f["rule"]) for f in o.get("findings", [])}
    s.check("HTML: <ol><li> -> procedure, aside.warning -> warning, <p> -> description, <pre> skipped",
            {("procedure", "one_instruction"), ("warning", "states_risk"), ("description", "active_voice")} <= got
            and not any(f["rule"] == "no_semicolon" for f in o.get("findings", [])), got)
    r = run("scripts/gates/check_language.py", [FX / "captions.srt"], env=env)
    o = out_json(r)
    s.check("captions (.srt): cues joined into sentences and judged as description (semicolon caught)",
            o.get("profiles_used") == {"description": 1} and
            any(f["rule"] == "no_semicolon" for f in o.get("findings", [])), o.get("profiles_used"))
    blk = tmp / "blocks.json"
    blk.write_text(json.dumps({"blocks": [{"text": "Restart the worker if it stops.", "profile": "procedure"},
                                          {"text": "The worker writes copies.", "profile": "description"}]}))
    r = run("scripts/gates/check_language.py", [blk], env=env)
    s.check("generic {blocks:[{text, profile}]} JSON accepted",
            any(f["rule"] == "condition_first" for f in out_json(r).get("findings", [])), r.stdout[:300])
    r = run("scripts/gates/check_language.py", [], env=env)
    s.check("no input -> SKIPPED (exit 2)", r.returncode == 2)
    r = run("scripts/gates/check_language.py", [md, "--profile", "legalese"], env=env)
    s.check("unknown non-invented profile -> ERROR (exit 3) naming the choices", r.returncode == 3 and
            "invented:" in r.stdout, r.stdout[:200])

    # ======================================================================== freedom: invented profiles
    kio = tmp / "kiosk.md"
    kio.write_text("1. Open the settings file in the editor right now.\n2. Restart it.\n")
    r = run("scripts/gates/check_language.py", [kio, "--profile", "invented:kiosk", "--base", "procedure",
                                               "--set", "max_words=8", "--reason", "a kiosk line shows 8 words"],
            env=env)
    o = out_json(r)
    s.check("invented profile accepted: base procedure, max_words=8, reason recorded, limit applied",
            r.returncode == 1 and any(f["rule"] == "length" and f["profile"] == "invented:kiosk"
                                      for f in o.get("findings", [])) and
            o.get("invented_profiles", [{}])[0].get("reason") == "a kiosk line shows 8 words", r.stdout[:300])
    kio.write_text("1. Open the file.\n2. Restart it.\n")
    r = run("scripts/gates/check_language.py", [kio, "--profile", "invented:kiosk", "--base", "procedure",
                                               "--set", "max_words=8", "--reason", "kiosk"], env=env)
    s.check("a good text under an invented profile passes (exit 0) — never failed for not being in the catalog",
            r.returncode == 0, r.stdout[:300])
    jp = tmp / "inv.json"
    jp.write_text(json.dumps({"profiles": {"invented:tweet": {"base": "description", "max_words": 6,
                                                               "reason": "a social card"}},
                              "blocks": [{"text": "The worker writes every resized copy to disk.",
                                          "profile": "invented:tweet"}]}))
    r = run("scripts/gates/check_language.py", [jp], env=env)
    s.check("invented profile declared inside the input JSON is honoured",
            any(f["profile"] == "invented:tweet" and f["rule"] == "length" for f in out_json(r).get("findings", [])),
            r.stdout[:300])

    # ======================================================================== notes that beat the reference
    r = run("scripts/gates/check_language.py", [kio.parent / "steps.md", "--dictionary", FX / "dictionary.txt"],
            env=env)
    o = out_json(r)
    s.check("local approved-word list: words outside it are NOTES (never review)",
            any(n["rule"] == "dictionary" for n in o.get("notes", [])) and
            not any(f["rule"] == "dictionary" for f in o.get("findings", [])), r.stdout[:200])
    s.check("without a word list the report says the dictionary was not checked",
            any("dictionary" in x for x in out_json(run("scripts/gates/check_language.py", [kio], env=env))
                .get("not_checked", [])))
    fill = cl.check_blocks([{"text": "Our robust engine seamlessly leverages the cache.", "profile": "description"}],
                           RULES)
    s.check("filler / marketing tone reported as a note in description", any(n["rule"] == "filler"
                                                                            for n in fill["notes"]), fill["notes"])

    # ======================================================================== D2 terms
    page_md = tmp / "terms_page.md"
    page_md.write_text("The `RateLimiter` caps calls. The Ratelimiter resets each minute. The Rate Limiter is "
                       "strict. The rate limiter keeps a count. The worker resizes copies.\n")
    voice = tmp / "terms_voice.txt"
    voice.write_text("The processor resizes the copy. A miss is rare, and misses cost a resize.\n")
    hv = sha(voice)
    r = run("scripts/gates/check_terms.py", ["--glossary", pc, "--aliases", FX / "aliases.json",
                                            "--page", page_md, "--voice", voice, "--captions", FX / "captions.srt"],
            env=env)
    o = out_json(r)
    fs = o.get("findings", [])
    syn = [f for f in fs if f["rule"] == "synonym" and f["found"] == "processor"]
    s.check("worker vs processor for one glossary entry is flagged (exit 1)", r.returncode == 1 and syn, fs[:2])
    s.check("the finding names the surfaces that disagree (page uses worker, voice uses processor)",
            syn and syn[0]["surfaces"].get("voice") and syn[0]["term_surfaces"].get("page"), syn)
    s.check("re-cased code name flagged (RateLimiter written Ratelimiter)",
            any(f["rule"] == "code_recased" and f["found"] == "Ratelimiter" for f in fs), fs)
    s.check("translated code name flagged (RateLimiter written as Title-Case 'Rate Limiter')",
            any(f["rule"] == "code_translated" and f["found"] == "Rate Limiter" for f in fs), fs)
    s.check("lower-case prose 'the rate limiter' is a note, not a finding",
            not any(f["found"] == "rate limiter" for f in fs) and
            any(n["found"] == "rate limiter" for n in o.get("notes", [])), o.get("notes"))
    s.check("glossary accepted forms (miss/misses) are not flagged",
            not any(f["found"].lower().startswith("miss") for f in fs if isinstance(f.get("found"), str)))
    s.check("a definition that names the synonym on purpose ('also called a processor') is not flagged",
            not any(f.get("surfaces", {}).get("page") for f in syn), syn)
    s.check("terms: inputs byte-identical", sha(voice) == hv and o.get("inputs_unchanged") is True)
    rep = ct.analyse({"page": "Use `max_retries` here. Set MAX_RETRIES low.", "voice": ""}, [], {}, [], RULES)
    s.check("back-ticked identifiers are learned as code names (max_retries vs MAX_RETRIES flagged)",
            any(f["rule"] == "code_recased" and f["found"] == "MAX_RETRIES" for f in rep["findings"]), rep)
    rep = ct.analyse({"page": "Copies go to the folder.", "voice": "The directory fills up."}, [], {}, [], RULES)
    s.check("no glossary: built-in synonym set (folder/directory) is only a note",
            not rep["findings"] and any(n["rule"] == "builtin_set" for n in rep["notes"]), rep)
    rep = ct.analyse({"page": "The worker resizes."}, [{"term": "worker"}], {"worker": ["processor"]}, [], RULES)
    s.check("terms: consistent text passes", not rep["findings"], rep)
    al2 = tmp / "al2.json"
    al2.write_text(json.dumps([{"term": "worker", "synonyms": ["processor"]}]))
    s.check("alias list in list-of-entries shape is read", ct.load_aliases(al2) == {"worker": ["processor"]})
    r = run("scripts/gates/check_terms.py", [], env=env)
    s.check("terms: no surfaces -> SKIPPED (exit 2)", r.returncode == 2)

    # ======================================================================== D3 truth
    tm = FX / "truth.md"
    ht = sha(tm)
    r = run("scripts/gates/check_truth.py", [tm, "--claims", FX / "truth_claims.json"], env=env)
    o = out_json(r)
    by_sent = {f["sentence"]: f for f in o.get("findings", [])}
    s.check("unbacked '40% faster' flagged with 'mark as estimate or cite'",
            "The new cache is 40% faster." in by_sent and
            "estimate" in by_sent["The new cache is 40% faster."]["message"] and
            "cite" in by_sent["The new cache is 40% faster."]["message"], list(by_sent))
    s.check("'about 40% faster (estimate)' passes", not any("about 40%" in k for k in by_sent), list(by_sent))
    s.check("a claim with a [[path:line]] chip passes", not any("200 ms" in k for k in by_sent))
    s.check("a claim matched in the claims manifest passes", not any("3x faster" in k for k in by_sent), list(by_sent))
    s.check("an absolute ('never loses a request') is flagged", by_sent.get("It never loses a request.", {})
            .get("rule") == "unbacked_absolute", list(by_sent))
    s.check("an instruction ('Never delete the cache file') is not a claim", "Never delete the cache file." not in by_sent)
    s.check("a superlative ('the fastest resizer') is flagged",
            any(f["rule"] == "unbacked_superlative" for f in by_sent.values()), list(by_sent))
    s.check("a citation to a claim id that is not in the manifest is flagged",
            any(f["rule"] == "unknown_claim_id" and f["claim_id"] == "c9" for f in o.get("findings", [])))
    s.check("an inferred mark passes ('(inferred)')", not any("stale" in k for k in by_sent))
    s.check("truth: input byte-identical, exit 1", sha(tm) == ht and r.returncode == 1 and o.get("inputs_unchanged"))
    r = run("scripts/gates/check_truth.py", [pc], env=env)
    o = out_json(r)
    sents = [f["sentence"] for f in o.get("findings", [])]
    s.check("page content.json: its own claims list backs '[c1]', the unbacked '40% faster' is flagged",
            any("40% faster" in x for x in sents) and not any("3 ms" in x for x in sents), sents)
    ok = tmp / "ok.md"
    ok.write_text("The worker resizes the copy. It keeps it for next time.\n")
    r = run("scripts/gates/check_truth.py", [ok], env=env)
    s.check("truth: text with no claims passes (exit 0)", r.returncode == 0, r.stdout[:200])

    # ======================================================================== REGRESSION: no existing gate moves
    build = tmp / "eb"
    (build / "_build").mkdir(parents=True)
    (build / "video").mkdir()
    (build / "demo.html").write_text("<!doctype html><html><head><title>Demo</title></head><body><h1>Demo</h1>"
                                     "<p>The cache keeps thumbnails; a miss pays for a resize.</p></body></html>")
    (build / "_build" / "narration.txt").write_text("The cache keeps thumbnails.\nA miss pays for a resize.\n")
    (build / "_build" / "beatmap.json").write_text(json.dumps(
        {"fps": 30, "sentences": [{"idx": 1, "text": "The cache keeps thumbnails.", "start": 0, "end": 2},
                                  {"idx": 2, "text": "A miss pays for a resize.", "start": 2, "end": 4}]}))
    (build / "_build" / "claims.json").write_text(json.dumps({"claims": [{"id": "c1", "text": "x",
                                                                           "ref": "nofile.py:3"}]}))
    (build / "demo.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nThe cache keeps thumbnails.\n\n"
                                    "2\n00:00:02,000 --> 00:00:04,000\nA miss pays for a resize.\n")
    shutil.copy(ROOT / "examples" / "example_scene.js", build / "video" / "scene.js")

    def gate_rows(pkg: Path) -> dict:
        cp = subprocess.run([PY, str(pkg / "scripts" / "run_gates.py"), str(build)], capture_output=True, text=True,
                            cwd=str(tmp), env={**os.environ, **env}, timeout=300)
        rows = {}
        for ln in cp.stdout.splitlines():
            m = re.match(r"^  (\S+)\s+(PASS|REVIEW|SKIPPED|ERROR|MISSING|\?\S*)\s*(.*)$", ln)
            if m:
                rows[m.group(1)] = (m.group(2), m.group(3).strip())
        return rows

    after = tmp / "pkg_after"
    before = tmp / "pkg_before"
    for dst in (after, before):
        shutil.copytree(ROOT / "scripts", dst / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "data", dst / "data", ignore=shutil.ignore_patterns("__pycache__"))
    for rel in OWN:
        (before / rel).unlink()
    ra, rb = gate_rows(after), gate_rows(before)
    volatile = {"env_detect", "disk", "perf", "quality"}
    # rows the supervisor wired for the 2.0 writing/claims/freedom layer (additive by design)
    new_rows = {"language:page", "language:narration", "terms", "truth", "page_content", "freedom", "claims:video"}
    diff = [k for k in (set(ra) | set(rb)) - new_rows if ra.get(k, ("?",))[0] != rb.get(k, ("?",))[0] or
            (k not in volatile and ra.get(k) != rb.get(k))]
    s.check("regression: run_gates on the example build gives identical results for every pre-existing gate "
            "with and without the new language/terms/truth files",
            ra and not diff and set(rb) - new_rows == set(ra) - new_rows, {"diff": diff, "rows": len(ra)})
    s.check("regression: the new checkers are wired into run_gates only as additional rows",
            {"language:page", "language:narration", "terms", "truth"} <= set(ra)
            and all(ra[k][0] in ("PASS", "REVIEW", "SKIPPED") for k in ("language:narration",)), sorted(ra))
    zips = [ROOT / "coordn8-v1.5.0.zip",
            Path(os.environ.get("C8_BASELINE_ZIP", str(ROOT.parent / "Coordn8_v2_handoff" / "_baseline" / "coordn8-v1.5.0.zip")))]
    zp = next((z for z in zips if z.exists()), None)
    if zp is None:
        s.check("v1.5.0 baseline not present here — v1.5.0 identity comparison SKIPPED (recorded)", True)
    else:
        old = tmp / "v150"
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if "scripts/" in n and not n.endswith("/"):
                    dest = old / n[n.index("scripts/"):]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(n))
        ro = gate_rows(old)
        same_code = []
        for name in ro:
            if name not in ra:
                continue
            base = name.split(":")[0]
            fname = {"env_detect": "env_detect.py", "quality": "quality.py"}.get(base)
            p_new = (ROOT / "scripts" / fname) if fname else (ROOT / "scripts" / "gates" / f"{base}.py")
            p_old = (old / "scripts" / fname) if fname else (old / "scripts" / "gates" / f"{base}.py")
            if p_new.exists() and p_old.exists() and p_new.read_bytes() == p_old.read_bytes():
                same_code.append(name)
        moved = [n for n in same_code if ro[n][0] != ra[n][0]]
        s.check("v1.5.0 identity: every gate whose code is unchanged since v1.5.0 gives the same status on the "
                "example build", ro and not moved, {"compared": same_code, "moved": moved})
        s.check("v1.5.0 baseline had no language/terms/truth gate (the checkers are new, beside the old ones)",
                not any((old / "scripts" / "gates" / f).exists()
                        for f in ("check_language.py", "check_terms.py", "check_truth.py")))

    # ======================================================================== hygiene
    blob = "\n".join((ROOT / p).read_text(encoding="utf-8") for p in OWN)
    def compiles(p):
        try:
            compile((ROOT / p).read_text(encoding="utf-8"), str(p), "exec")
            return True
        except SyntaxError:
            return False
    s.check("Python 3.9 safe: every new script compiles", all(compiles(p) for p in OWN if p.endswith(".py")))
    s.check("no hosted-service call in the new checkers (no http client imports)",
            not re.search(r"\bimport\s+(?:requests|urllib\.request|http\.client)|urlopen\(", blob))

sys.exit(s.finish())
