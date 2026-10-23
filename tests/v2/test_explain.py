#!/usr/bin/env python3
"""Explanation craft: audience framing (I1), shapes (I2), fenced analogies and one example
(I3), end where they can act (I4), stand down for experts (I5).

Hermetic: fixtures are written to a temp dir; nothing touches the user's home.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

AUD_JSON = ROOT / "data" / "audiences.json"
SHAPES_JSON = ROOT / "data" / "explain_shapes.json"
AUD_PY = "scripts/design/audience.py"
CHK_PY = "scripts/gates/check_explain.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FX = {
    # ---------------- I2 shapes
    "err_pos.md": (
        "The line that matters is `PermissionError: [Errno 13] Permission denied: '/var/log/app.log'`.\n"
        "In plain words, the program tried to write its log file and the operating system said no.\n"
        "It happens because the service runs as the `app` user, and that file is owned by root.\n"
        "To fix it, change the owner with `chown app /var/log/app.log`, or set `LOG_PATH` to a folder the "
        "service can write.\nRun the service again and the error is gone.\n"),
    "err_neg.md": "Something went wrong when the service started.\nRestart it and see if it works.\n",
    "err_two_neg.md": (
        "The line that matters is `ConnectionRefusedError: [Errno 111] Connection refused`.\n"
        "This means the client knocked on the database port and nothing answered.\n"
        "There are two likely causes: the database is not running, or the client points at the wrong port.\n"
        "Change the port in `config.yaml` or start the database.\n"),
    "err_two_pos.md": (
        "The line that matters is `ConnectionRefusedError: [Errno 111] Connection refused`.\n"
        "This means the client knocked on the database port and nothing answered.\n"
        "There are two likely causes: the database is not running, or the client points at the wrong port.\n"
        "To tell them apart, run `pg_isready`: if it says the server is accepting connections, the port is wrong.\n"
        "Change the port in `config.yaml` or start the database.\n"),
    "err_dump.md": (
        "Here is the log:\n\n```\n2026-10-01 ERROR worker failed\nTraceback (most recent call last):\n"
        "  File \"a.py\", line 3, in <module>\nValueError: invalid literal for int()\nERROR job aborted\n```\n\n"
        "In plain words, the worker read text where it expected a number.\n"
        "It happens because the input file has a header row.\nRemove the header row and run the job again.\n"),
    "code.py": (
        "def load_config(path):\n    raw = read_file(path)\n    data = parse_yaml(raw)\n    validate(data)\n"
        "    log.debug(\"loaded\")\n    return merge_defaults(data)\n"),
    "fn_pos.md": (
        "`load_config` turns a YAML file on disk into the settings dict the rest of the app reads.\n\n"
        "1. `read_file` pulls the raw text from the path.\n2. `parse_yaml` turns that text into a dict.\n"
        "3. `merge_defaults` fills in every setting the file left out.\n\n"
        "The rest is validation and a debug log line, skipped here.\n"
        "Open `config.py` and change a default to see it flow through.\n"),
    "fn_neg.md": ("First it reads the file. Then it parses YAML. Then it validates. Then it logs. "
                  "Then it merges defaults. Finally it returns the dict.\n"),
    "fn_badcode.md": (
        "`load_config` turns a YAML file on disk into the settings dict the rest of the app reads.\n\n"
        "1. `merge_defaults` fills in every setting the file left out.\n2. `fetch_remote` downloads overrides.\n"
        "3. `read_file` pulls the raw text from the path.\n\nThe rest is validation, skipped here.\n"
        "Open `config.py` and change a default to see it flow through.\n"),
    "fn_example_invented.md": (
        "`load_config` turns a YAML file on disk into the settings dict the rest of the app reads.\n\n"
        "1. `read_file` pulls the raw text from the path.\n2. `parse_yaml` turns that text into a dict.\n\n"
        "For example, imagine a pizza shop that writes its menu on a whiteboard, except nobody reads it.\n\n"
        "The rest is validation, skipped here.\nOpen `config.py` and change a default to see it flow through.\n"),
    "fn_example_code.md": (
        "`load_config` turns a YAML file on disk into the settings dict the rest of the app reads.\n\n"
        "1. `read_file` pulls the raw text from the path.\n2. `parse_yaml` turns that text into a dict.\n\n"
        "For example, `merge_defaults` fills in the port when the file leaves it out.\n\n"
        "The rest is validation, skipped here.\nOpen `config.py` and change a default to see it flow through.\n"),
    "arch_pos.md": (
        "Follow one upload from the browser to the thumbnail on screen.\n\n"
        "1. The `UploadHandler` receives the file and checks its size.\n"
        "2. Then it puts the file on the `ResizeQueue`, which holds work until a worker is free.\n"
        "3. Next the `ThumbWorker` pulls it off the queue and shrinks it to 200 pixels.\n"
        "4. It writes the thumbnail to the `media` bucket and records its address.\n\n"
        "Finally, the browser gets the thumbnail address and shows the picture.\n"
        "Everything off this path can wait, including the retry branch.\n"
        "Try uploading a large photo and watch the queue length change.\n"),
    "arch_neg.md": (
        "The system has an `Api`, a `Queue`, and a `Worker`.\nThe `Api` talks to the `Queue`.\n"
        "Meanwhile, another path sends billing events to the `Ledger`.\nThat's how it all fits together.\n"),
    "concept_pos.md": (
        "A cache is a small, fast store that keeps answers already worked out, so nobody has to work them out again.\n"
        "Think of it as a sticky note on the fridge with the pizza place's number on it, except the note can go out "
        "of date when the number changes.\nThe app checks the cache before it asks the slow database.\n"
        "If the answer is there, it uses it; if not, it asks the database and keeps a copy.\n"
        "This matters because a page that used to take two seconds now loads in a blink.\n"
        "When a page feels slow, check whether it is being cached.\n"),
    "concept_neg.md": ("Imagine a library where every book has a sticky note.\nA cache stores data.\n"
                       "It uses keys and values.\n"),
    "concept_order.md": (
        "This matters because a page that used to take two seconds now loads in a blink.\n"
        "A cache is a small, fast store that keeps answers already worked out.\n"
        "Think of it as a sticky note on the fridge, except the note can go out of date.\n"
        "The app checks the cache before it asks the slow database.\n"
        "When a page feels slow, check whether it is being cached.\n"),
    "term_pos.md": ("TTL (time to live) is how long a cached item lives before it is deleted. "
                    "Set it to 60 and the item is gone a minute later.\n"),
    "term_neg.md": (
        "# What TTL means\n\nTTL (time to live) is how long a cached item lives before it is deleted. Every store "
        "handles it a little differently, and there is a long history to how it came about across many systems over "
        "the years. Some systems refresh it on read, others only on write, and a few let each key choose. Defaults "
        "vary widely between products and versions, and the exact semantics depend on configuration. Set it to 60 "
        "and the item is gone a minute later.\n"),
    "custom.md": ("The goal is one clean release.\n\nFirst, freeze the branch. Then run the full suite.\n\n"
                  "You are done when the tag is pushed and the notes are posted. Push the tag now.\n"),
    "custom_bad.md": "Push the tag now.\n",
    # ---------------- I3
    "an_neg.md": "An index is like a book's table of contents.\n\nOpen the table settings to see it.\n",
    "an_pos.md": ("An index is like a book's table of contents, except the database keeps several and each one "
                  "slows down writes a little.\n\nOpen the table settings to see it.\n"),
    "an_pos_next.md": ("Think of a queue as a line at a bakery. The difference is that nobody can cut in.\n\n"
                       "Watch the line length on the dashboard.\n"),
    "two_ex.md": ("Retries help with flaky networks. For example, a phone on a train drops signal. For instance, a "
                  "laptop on hotel wifi loses packets.\n\nTry turning retries on for the upload call.\n"),
    "one_ex.md": ("Retries help with flaky networks. For example, a phone on a train drops signal for a few seconds "
                  "and the retry quietly covers the gap.\n\nTry turning retries on for the upload call.\n"),
    "simp.md": "Roughly speaking, the index is a sorted copy of one column.\n\nOpen the table settings to see it.\n",
    # ---------------- I4
    "cav_neg.md": ("The uploader sends each file once. Note that the function also logs a line at debug level.\n\n"
                   "Try it on a small file first.\n"),
    "cav_pos.md": ("The uploader sends each file once. Note that a second upload overwrites the old file and cannot "
                   "be undone.\n\nTry it on a small file first.\n"),
    "end_neg.md": ("The cache keeps recent answers close by so pages load fast. In summary, the cache makes pages "
                   "faster.\n"),
    "end_pos.md": ("The cache keeps recent answers close by so pages load fast. You can now turn it on for the search "
                   "page and compare load times.\n"),
    "cut_none.md": "The uploader sends each file once.\n\nTry it on a small file first.\n",
    "cut_pos.md": ("The uploader sends each file once and the rest of the pipeline is not covered here, including "
                   "the retry policy and metrics.\n\nTry it on a small file first.\n"),
    "cut_unnamed.md": ("The uploader sends each file once; the rest is not covered here.\n\n"
                       "Try it on a small file first.\n"),
    "page.json": json.dumps({"sections": [
        {"title": "What it is", "text": "The uploader turns a photo into a thumbnail. Open the upload page to try it."},
        {"title": "Why it matters", "text": "Thumbnails keep the gallery fast. That is the main benefit."}]}),
    "page_ok.json": json.dumps({"sections": [
        {"title": "What it is", "text": "The uploader turns a photo into a thumbnail. Open the upload page to try it."},
        {"title": "Why it matters", "text": "Thumbnails keep the gallery fast. Compare the gallery with and without "
                                            "them."}]}),
    "narr_ok.json": json.dumps({"lines": [
        {"text": "Every upload starts in the browser."},
        {"text": "The handler checks the size and hands it to the resize worker."},
        {"text": "You can now follow any upload from click to thumbnail."}]}),
    "narr_bad.json": json.dumps(["Every upload starts in the browser.",
                                 "The handler checks the size and hands it to the resize worker.",
                                 "And that's the whole story."]),
    "page.html": ("<html><body><h2>What it is</h2><p>The uploader turns a photo into a thumbnail.</p>"
                  "<p>Open the upload page to try it.</p><script>var like = 1;</script></body></html>"),
    # ---------------- I5
    "expert_neg.md": ("The planner reads every row when the lookup shortcut is missing.\n\n"
                      "Check the plan with EXPLAIN before adding anything.\n"),
    "expert_pos.md": ("The planner falls back to a full table scan when no index covers the predicate.\n\n"
                      "Check the plan with EXPLAIN before adding anything.\n"),
    "staff.json": json.dumps({"audience": "staff engineers on the database team",
                              "subject": "how our query planner picks an index for range scans"}),
    "jar_neg.md": "The app keeps a cache so pages load faster.\n\nOpen the settings page to clear it.\n",
    "jar_pos.md": ("The app keeps a cache (a stash of answers it already worked out) so pages load faster.\n\n"
                   "Open the settings page to clear it.\n"),
    "acr_neg.md": "The phone asks DNS for the address first.\n\nOpen the network settings to see it.\n",
    # ---------------- I1
    "biz_neg.md": ("The migration moves billing to the new queue. It uses three workers and a dead-letter topic.\n\n"
                   "The workers scale with load.\n"),
    "biz_pos.md": ("The migration cuts our messaging bill by $40k a month and removes the outage risk we hit twice "
                   "last quarter. This means we should fund it this quarter.\n\nApprove the two engineers for six "
                   "weeks.\n"),
    "biz_code.md": ("The migration cuts our messaging bill by $40k a month. This means we should fund it.\n\n"
                    "```\nworkers: 3\n```\n\nApprove the two engineers for six weeks.\n"),
    "mech_neg.md": "It loops over every order and sums the totals.\n\nOpen the invoice page to check a total.\n",
    "mech_pos.md": ("This function totals an order so the invoice shows the right amount.\n\n"
                    "Open the invoice page to check a total.\n"),
    "throat.md": ("Great question! A cache keeps recent answers close by so pages load fast.\n\n"
                  "Open the settings page to clear it.\n"),
    "child_long.md": ("The little sensor on the roof measures how warm the air is and how fast the wind is blowing "
                      "every single minute of the day and night without ever stopping to rest.\n\n"
                      "Look at the sensor on the roof next time you walk past the school.\n"),
    "child_short.md": ("The sensor on the roof feels the air. It sends a number every minute.\n\n"
                       "Look for it next time you walk past.\n"),
    "bad.json": "{not json",
}


def main() -> int:
    s = Suite("explain")
    cat = json.loads(AUD_JSON.read_text(encoding="utf-8"))
    shapes = json.loads(SHAPES_JSON.read_text(encoding="utf-8"))
    A = load_module("c8_audience_t", ROOT / AUD_PY)

    # ============================================================ I1 catalog
    roles = {r["id"]: r for r in cat["roles"]}
    need = {"engineer", "senior-sme", "manager", "director", "executive", "designer", "product", "newcomer",
            "external-developer", "auditor", "student", "non-technical-family", "child"}
    s.check("audiences: at least 12 roles, including every role the requirement names",
            len(roles) >= 12 and need <= set(roles), sorted(need - set(roles)))
    fields = ["they_care_about", "frame_around", "register", "entry_point", "depth", "allowed_analogy_sources",
              "avoid", "business_lead", "expert_fields", "keywords", "default_level", "levels", "video_pace"]
    missing = [(r["id"], f) for r in cat["roles"] for f in fields if f not in r
               or (f not in ("business_lead", "expert_fields") and not r[f])]
    s.check("audiences: every role carries every field (care, frame, register, entry, depth, analogy sources, "
            "avoid, business_lead, expert_fields)", not missing, missing)
    order = [lv["id"] for lv in sorted(cat["levels"], key=lambda x: x["order"])]
    s.check("audiences: levels run child -> teen -> lay-adult -> informed-adult -> practitioner -> specialist",
            order == ["child", "teen", "lay-adult", "informed-adult", "practitioner", "specialist"]
            and all(lv.get("sentence_words_max") and lv.get("analogy_sources") for lv in cat["levels"]), order)
    bad_lv = [(r["id"], lv) for r in cat["roles"] for lv in r["levels"] + [r["default_level"]] if lv not in order]
    s.check("audiences: every role's levels exist and its default level is one of its own levels",
            not bad_lv and all(r["default_level"] in r["levels"] for r in cat["roles"]), bad_lv)
    biz = [r for r in cat["roles"] if r.get("business")]
    s.check("audiences: every business role leads with the decision ('this means we should')",
            len(biz) >= 3 and all("we should" in (r.get("business_lead") or "").lower() for r in biz)
            and all(r.get("business_lead") is None for r in cat["roles"] if not r.get("business")),
            [r["id"] for r in biz])
    # agreement with the brain's audience table (read-only)
    pb = (ROOT / "engine" / "PLAYBOOK.md").read_text(encoding="utf-8")
    rows = {}
    for line in pb.splitlines():
        m = re.match(r"^\|\s*(New engineer|Senior / SME|Leadership|External / OSS|Auditor / reviewer)\s*\|(.*)\|\s*$", line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            rows[m.group(1)] = cells
    disagree = []
    for r in cat["roles"]:
        row = rows.get(r.get("playbook_row") or "")
        if r.get("playbook_row"):
            if not row or [r["entry_point"], r["depth"], r["register"], r["video_pace"]] != row[:4]:
                disagree.append((r["id"], row))
    covered = {r.get("playbook_row") for r in cat["roles"]}
    s.check("audiences: the five audience rows of the brain's audience table match the data (entry point, depth, "
            "register, pace)", len(rows) == 5 and not disagree and set(rows) <= covered, disagree or rows.keys())
    cb = load_module("c8_content_ban_t", ROOT / "scripts" / "gates" / "content_ban.py")
    greet = cb.PATTERNS["audience_roleplay_greeting"]
    blob = json.dumps(cat, ensure_ascii=False)
    s.check("audiences: no catalog text greets or role-plays the reader (content-ban greeting pattern)",
            not greet.search(blob) and "greets" in json.dumps(cat["doctrine"]),
            greet.search(blob).group(0) if greet.search(blob) else "")
    s.check("audiences: the doctrine forbids defaulting to a child and reads 'explain like I'm five' as keep-it-simple",
            any("never defaulted to a child" in d for d in cat["doctrine"])
            and any("like I'm five" in d for d in cat["doctrine"]), cat["doctrine"])

    # ============================================================ I1 inference
    def inf(b):
        return A.infer(b, cat)

    r = inf({"brief": "Onboarding doc for new grads joining the payments team. They know Python but not our "
                      "services.", "goal": "start contributing in their first month"})
    s.check("audience: new-grad onboarding brief -> newcomer, reason names the signal",
            r["role"] == "newcomer" and "new grads" in r["reason"] and r["confidence"] >= 0.6 and not r["ask"], r)
    r = inf({"brief": "Ten-minute walkthrough for the VP's quarterly funding review: should we fund the migration "
                      "to the new queue?"})
    s.check("audience: VP funding review brief -> executive (business lead), reason names the signal",
            r["role"] == "executive" and "VP" in r["reason"] and "funding review" in r["reason"]
            and "we should" in (r["profile"]["business_lead"] or "").lower(), r["reason"])
    r = inf({"brief": "README companion for open-source contributors who found the project on GitHub and want to "
                      "send a first patch."})
    s.check("audience: external OSS brief -> external-developer, reason names the signal",
            r["role"] == "external-developer" and "open-source contributors" in r["reason"], r["reason"])
    r = inf({"brief": "For the compliance auditor reviewing every decision branch of the refund engine; every claim "
                      "needs a reference."})
    s.check("audience: auditor brief -> auditor, reason names the signal",
            r["role"] == "auditor" and "auditor" in r["reason"]
            and r["profile"]["entry_point"] == "completeness of the decision surface", r["reason"])
    r = inf({"brief": "A short video for the kids' science club (ages 8-11) about how the weather sensor sends its "
                      "readings."})
    s.check("audience: kids' science club (ages 8-11) -> child level, reason cites the ages",
            r["role"] == "child" and r["level"] == "child" and "8-11" in r["reason"]
            and r["profile"]["sentence_words_max"] <= 12, r["reason"])
    staff = inf({"audience": "staff engineers on the database team",
                 "subject": "how our query planner picks an index for range scans"})
    s.check("audience: staff engineers on the database team -> senior-sme at specialist level, reason says why",
            staff["role"] == "senior-sme" and staff["level"] == "specialist" and "staff engineers" in staff["reason"],
            staff["reason"])
    s.check("audience: an expert in the subject's own field stands down and keeps the field's terms",
            staff["stand_down"] and "databases" in staff["expert_in"] and "index" in staff["keep_terms"]
            and "query plan" in staff["keep_terms"] and staff["profile"]["allowed_analogy_sources"]
            == ["their own field, structurally, or none"], staff)
    doc = inf({"audience": "doctors", "subject": "how the hospital's database indexes patient records"})
    s.check("audience: an expert in another field is simple about the subject, normal about their own "
            "(no stand-down)", not doc["stand_down"] and "medicine" in doc["expert_in"]
            and "databases" in doc["simplify_fields"] and "triage" in doc["keep_terms"]
            and any("medicine" in a for a in doc["profile"]["allowed_analogy_sources"]), doc["reason"])
    unclear = inf({"brief": "Make a video about this repo."})
    s.check("audience: an unclear brief returns a plain Phase-0 question and a low-confidence guess, never child",
            bool(unclear["ask"]) and unclear["ask"].endswith("?") and unclear["confidence"] < 0.5
            and unclear["role"] != "child" and unclear["level"] != "child"
            and not re.search(r"\b[A-Z]\d\b", unclear["ask"]), unclear)
    # the four-letter abbreviation is assembled at run time so no shipped file carries it as a token
    abbr = "".join(["E", "L", "I", "5"])
    eli = inf({"brief": abbr + " how this cache works."})
    eli2 = inf({"brief": "Explain like I'm 5 years old how the retry loop works."})
    s.check("audience: 'explain like I'm five' and its abbreviation are read as keep-it-simple, never as a five-year-old",
            eli["level"] != "child" and eli["role"] != "child" and eli["simplicity_request"]
            and eli2["level"] != "child" and eli2["role"] != "child" and "keep it simple" in eli["reason"],
            (eli["role"], eli["level"], eli2["role"], eli2["level"]))
    phrases = {"engineer": "the backend engineers", "senior-sme": "the principal engineers and architects",
               "manager": "my engineering managers", "director": "the directors in senior leadership",
               "executive": "the CFO and the board", "designer": "the product designers",
               "product": "the product managers", "newcomer": "new hires during onboarding",
               "external-developer": "open-source contributors", "auditor": "the internal audit team",
               "student": "university students", "non-technical-family": "my mom and dad",
               "colleague": "colleagues in partner teams", "general-public": "the general public"}
    wrong = []
    for rid, ph in phrases.items():
        got = inf({"brief": f"A walkthrough of the billing service for {ph}."})
        if got["role"] != rid or got["level"] == "child":
            wrong.append((rid, got["role"], got["level"]))
    s.check("audience: each catalog role is picked back from a brief naming it (round trip)", not wrong, wrong)
    sweep = ["Explain how the worker spawns a child process and retries.", "Walk through the parent/child node "
             "tree in the renderer.", "Make it simple.", "Explain it to me like I'm five.", "Dumb it down please.",
             "Video for anyone curious about compilers.", "Explain the queue."]
    sweep += [f"A walkthrough of the billing service for {ph}." for ph in phrases.values()]
    kids = [b for b in sweep if inf({"brief": b})["level"] == "child" or inf({"brief": b})["role"] == "child"]
    s.check("audience: no brief without a child signal is ever inferred as child (role sweep)", not kids, kids)
    inv = inf({"audience_role": "invented: marine biologists", "audience_reason": "the client is an aquarium",
               "subject": "how the tank sensor uploads readings"})
    s.check("audience: an invented audience is accepted with the caller's reason",
            inv["role"] == "invented:marine biologists" and "aquarium" in inv["reason"] and inv["profile"], inv["reason"])
    s.check("audience: profile carries design guidance and never a greeting",
            all(k in staff["profile"] for k in ("they_care_about", "frame_around", "register", "entry_point", "depth",
                                                 "allowed_analogy_sources", "avoid", "design_only"))
            and not greet.search(json.dumps([inf({"brief": f"for {p}"})["profile"] for p in phrases.values()])),
            staff["profile"].keys())

    with tmpdir() as d:
        d = Path(d)
        env = sandbox_env(d)
        fx = d / "fx"
        fx.mkdir()
        for name, text in FX.items():
            (fx / name).write_text(text, encoding="utf-8")
        (fx / "brief_ok.json").write_text(json.dumps({"brief": "Ten-minute walkthrough for the VP's funding review."}))
        (fx / "brief_unclear.json").write_text(json.dumps({"brief": "Make a video about this repo."}))
        p1 = run(AUD_PY, [fx / "brief_ok.json", "--json"], env=env, timeout=60)
        p2 = run(AUD_PY, [fx / "brief_unclear.json", "--json"], env=env, timeout=60)
        try:
            j1, j2 = json.loads(p1.stdout), json.loads(p2.stdout)
        except ValueError:
            j1 = j2 = {}
        s.check("audience: CLI --json prints valid JSON and exits 0 decided / 1 when it asks",
                p1.returncode == 0 and j1.get("role") == "executive" and p2.returncode == 1 and j2.get("ask"),
                (p1.returncode, p2.returncode, p1.stderr[-200:]))

        def chk(name, *args):
            p = run(CHK_PY, [fx / name, *args], env=env, timeout=60)
            try:
                rep = json.loads(p.stdout.splitlines()[0])
            except (ValueError, IndexError):
                rep = {"status": "UNPARSEABLE", "stdout": p.stdout[-300:], "stderr": p.stderr[-300:]}
            return p.returncode, rep

        def rules(rep):
            return [f["rule"] for f in rep.get("findings", [])]

        def ok(rep):
            return rep.get("status") == "PASS" and not rep.get("findings")

        eng = ("--audience", "engineer")

        # ============================================================ I2
        sh = {x["id"]: x for x in shapes["shapes"]}
        s.check("shapes: error, function, architecture and concept each have ordered parts and detection cues",
                all(k in sh and len(sh[k]["parts"]) >= 3 and sh[k]["detect_cues"] for k in
                    ("error", "function", "architecture", "concept")), list(sh))
        s.check("shapes: the parts follow the requirement wording (quote/translate/cause/change; job/steps/skipped; "
                "one path/hops/end; what/analogy/details/so what)",
                [p["id"] for p in sh["error"]["parts"]] == ["quote", "translate", "cause", "change"]
                and [p["id"] for p in sh["function"]["parts"]] == ["job", "steps", "skipped"]
                and [p["id"] for p in sh["architecture"]["parts"]] == ["one_path", "hops", "end"]
                and [p["id"] for p in sh["concept"]["parts"]] == ["what", "analogy", "details", "so_what"],
                {k: [p["id"] for p in v["parts"]] for k, v in sh.items()})
        rc, rep = chk("err_pos.md", "--shape", "error", *eng)
        s.check("explain: error positive (quote -> translate -> cause -> change) passes",
                rc == 0 and ok(rep) and rep["structure"]["order"] == ["quote", "translate", "cause", "change"], rep)
        rc, rep = chk("err_neg.md", "--shape", "error", *eng)
        s.check("explain: error negative (no quote, no translation, no cause) is flagged",
                rc == 1 and rules(rep).count("I2.missing_part") >= 3, rules(rep))
        rc, rep = chk("err_two_neg.md", "--shape", "error", *eng)
        s.check("explain: two likely causes without how to tell them apart is flagged",
                "I2.two_causes_no_tell_apart" in rules(rep), rules(rep))
        rc, rep = chk("err_two_pos.md", "--shape", "error", *eng)
        s.check("explain: two likely causes with how to tell them apart passes", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("err_dump.md", "--shape", "error", *eng)
        s.check("explain: a pasted multi-line log is flagged (quote the one line that carries the meaning)",
                "I2.error_quote_one_line" in rules(rep), rules(rep))
        rc, rep = chk("fn_pos.md", "--shape", "function", *eng, "--code", fx / "code.py")
        s.check("explain: function positive (job first, 3 parts in run order, boilerplate skipped) passes",
                rc == 0 and ok(rep) and rep["structure"]["parts"]["steps"]["count"] == 3
                and rep["structure"]["parts"]["steps"]["missing_in_code"] == [], rep.get("findings"))
        rc, rep = chk("fn_neg.md", "--shape", "function", *eng)
        s.check("explain: function negative (too many parts, nothing said about what was skipped) is flagged",
                "I2.steps_count" in rules(rep) and "I2.missing_part" in rules(rep), rules(rep))
        rc, rep = chk("fn_badcode.md", "--shape", "function", *eng, "--code", fx / "code.py")
        s.check("explain: with --code, steps out of run order and names not in the code are flagged",
                "I2.steps_not_in_run_order" in rules(rep) and "I2.names_not_in_code" in rules(rep), rules(rep))
        rc, rep = chk("arch_pos.md", "--shape", "architecture", *eng)
        s.check("explain: architecture positive (one upload traced end to end, pieces named by what they do) passes",
                rc == 0 and ok(rep) and rep["structure"]["parts"]["hops"]["count"] >= 3, rep.get("findings"))
        rc, rep = chk("arch_neg.md", "--shape", "architecture", *eng)
        s.check("explain: architecture negative (no single path, a second path, pieces only named) is flagged",
                {"I2.missing_part", "I2.more_than_one_path", "I2.piece_named_not_described"} <= set(rules(rep)),
                rules(rep))
        rc, rep = chk("concept_pos.md", "--shape", "concept", "--audience", "general-public")
        s.check("explain: concept positive (what -> fenced analogy -> details -> so what) passes",
                rc == 0 and ok(rep) and rep["structure"]["order"] == ["what", "analogy", "details", "so_what"],
                rep.get("findings"))
        rc, rep = chk("concept_neg.md", "--shape", "concept", "--audience", "general-public")
        s.check("explain: concept negative (no definition, no so-what, analogy unfenced) is flagged",
                rules(rep).count("I2.missing_part") >= 2 and "I3.analogy_unfenced" in rules(rep), rules(rep))
        rc, rep = chk("concept_order.md", "--shape", "concept", "--audience", "general-public")
        s.check("explain: parts out of order (so-what before what) are flagged", "I2.order" in rules(rep), rules(rep))
        rc, rep = chk("term_neg.md", "--question", "What does TTL mean?")
        rc2, rep2 = chk("term_pos.md", "--question", "What does TTL mean?")
        s.check("explain: length follows the question (a one-term question with a long, headed answer is flagged; "
                "two sentences pass)", "plain.length_follows_question" in rules(rep) and rc2 == 0 and ok(rep2),
                (rules(rep), rules(rep2)))
        rc, rep = chk("term_pos.md", "--shape", "auto", "--question", "What does TTL mean?")
        s.check("explain: shape auto-detects from the question", rep.get("shape") == "term"
                and rep.get("shape_source") == "auto", (rep.get("shape"), rep.get("shape_source")))
        rc, rep = chk("custom.md", "--shape", "invented:release-checklist", "--parts",
                      r"goal=the goal|\baim\b;steps=\bfirst\b|\bthen\b;done=you are done when", *eng)
        rc2, rep2 = chk("custom_bad.md", "--shape", "invented:release-checklist", "--parts",
                        r"goal=the goal|\baim\b;steps=\bfirst\b|\bthen\b;done=you are done when", *eng)
        s.check("explain: a caller-invented shape with its own parts is accepted and judged",
                rc == 0 and ok(rep) and rep["shape_source"] == "custom" and rep["shape"] == "invented:release-checklist"
                and rules(rep2).count("I2.missing_part") == 3, (rules(rep), rules(rep2)))
        rc, rep = chk("custom.md", "--shape", "invented:release-checklist", *eng)
        s.check("explain: an invented shape with no parts is accepted (structure unjudged, never refused)",
                rc == 0 and rep.get("status") == "PASS" and rep.get("shape_source") == "unjudged" and rep["notes"],
                rep.get("notes"))
        rc, rep = chk("page.html", *eng)
        rc2, rep2 = chk("narr_ok.json", *eng)
        s.check("explain: HTML, page-section JSON and narration-line JSON inputs are all read",
                rc == 0 and rep.get("mode") == "page" and rc2 == 0 and rep2.get("mode") == "narration"
                and rep2["stats"]["sentences"] == 3, (rep.get("mode"), rep2.get("mode")))

        rc, rep = chk("jar_pos.md", "--audience", "invented: marine biologists")
        s.check("explain: an invented audience is accepted by the checker (judged on outcomes only)",
                rc == 0 and ok(rep) and rep["audience"]["role"] == "invented: marine biologists", rep.get("audience"))

        # ============================================================ I3
        rc, rep = chk("an_neg.md", *eng)
        s.check("explain: analogy without its limit is flagged", "I3.analogy_unfenced" in rules(rep), rules(rep))
        rc, rep = chk("an_pos.md", *eng)
        rc2, rep2 = chk("an_pos_next.md", *eng)
        s.check("explain: analogy with its limit (same sentence or within two sentences) passes",
                rc == 0 and ok(rep) and rc2 == 0 and ok(rep2), (rules(rep), rules(rep2)))
        rc, rep = chk("two_ex.md", *eng)
        s.check("explain: two examples for one point are flagged", "I3.two_examples" in rules(rep), rules(rep))
        rc, rep = chk("one_ex.md", *eng)
        s.check("explain: one example passes", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("simp.md", *eng)
        s.check("explain: a simplification marker is reported as a trade-off",
                "I3.simplification_tradeoff" in rules(rep) and rep["tradeoffs"]
                and rep["tradeoffs"][0]["marker"].lower() == "roughly", rep.get("tradeoffs"))
        rc, rep = chk("fn_example_invented.md", "--shape", "function", *eng, "--code", fx / "code.py")
        rc2, rep2 = chk("fn_example_code.md", "--shape", "function", *eng, "--code", fx / "code.py")
        s.check("explain: with --code, an invented example is flagged and an example from the code passes",
                "I3.example_not_from_code" in rules(rep) and "I3.example_not_from_code" not in rules(rep2)
                and rc2 == 0, (rules(rep), rules(rep2)))

        # ============================================================ I4
        rc, rep = chk("end_neg.md", *eng)
        s.check("explain: ending on a summary is flagged", "I4.ending_not_actionable" in rules(rep)
                and "summary" in rep["findings"][-1]["message"], rules(rep))
        rc, rep = chk("end_pos.md", *eng)
        s.check("explain: ending on what the reader can now do passes", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("page.json", *eng)
        rc2, rep2 = chk("page_ok.json", *eng)
        s.check("explain: every page section is checked for an actionable ending",
                rules(rep) == ["I4.ending_not_actionable"] and "Why it matters" in rep["findings"][0]["where"]
                and rc2 == 0, (rules(rep), rules(rep2)))
        rc, rep = chk("narr_bad.json", *eng)
        rc2, rep2 = chk("narr_ok.json", *eng)
        s.check("explain: narration is judged on its last beat", "I4.ending_not_actionable" in rules(rep)
                and rc2 == 0, (rules(rep), rules(rep2)))
        rc, rep = chk("cav_neg.md", *eng)
        s.check("explain: a caveat with no real cost is flagged as unneeded", "I4.caveat_without_cost" in rules(rep),
                rules(rep))
        rc, rep = chk("cav_pos.md", *eng)
        s.check("explain: a caveat whose skipping costs something real (data, irreversible) passes",
                rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("cut_none.md", *eng, "--cut", "retry policy, metrics")
        s.check("explain: content was cut but nothing says so -> flagged", "I4.skipped_not_said" in rules(rep),
                rules(rep))
        rc, rep = chk("cut_pos.md", *eng, "--cut", "retry policy, metrics")
        s.check("explain: a skipped statement that names the cut items passes", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("cut_unnamed.md", *eng, "--cut", "retry policy, metrics")
        s.check("explain: a skipped statement that leaves a cut item unnamed is flagged",
                "I4.skipped_item_unnamed" in rules(rep), rules(rep))

        # ============================================================ I5
        rc, rep = chk("expert_neg.md", "--audience", fx / "staff.json")
        s.check("explain: expert audience: a lay paraphrase of a field term is flagged (talking down)",
                "I5.talking_down" in rules(rep) and "index" in rep["findings"][0]["message"]
                and rep["audience"]["stand_down"], rules(rep))
        rc, rep = chk("expert_pos.md", "--audience", fx / "staff.json")
        s.check("explain: expert audience: the field's own terms, undefined, pass", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("expert_neg.md", "--expert-field", "databases")
        s.check("explain: --expert-field alone switches on the talking-down check", "I5.talking_down" in rules(rep),
                rules(rep))
        rc, rep = chk("jar_neg.md", "--audience", "non-technical-family")
        rc2, rep2 = chk("acr_neg.md", "--audience", "non-technical-family")
        s.check("explain: non-expert audience: undefined jargon (a field term, an acronym) is flagged",
                "I5.jargon_undefined" in rules(rep) and "I5.jargon_undefined" in rules(rep2), (rules(rep), rules(rep2)))
        rc, rep = chk("jar_pos.md", "--audience", "non-technical-family")
        s.check("explain: non-expert audience: jargon defined in the same breath passes", rc == 0 and ok(rep),
                rules(rep))

        # ============================================================ I1 checks on text
        rc, rep = chk("biz_neg.md", "--audience", "executive")
        s.check("explain: business audience without impact first and without a decision is flagged",
                {"I1.business_impact_not_first", "I1.business_no_decision"} <= set(rules(rep)), rules(rep))
        rc, rep = chk("biz_pos.md", "--audience", "executive")
        s.check("explain: business audience with impact first and 'this means we should' passes",
                rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("biz_code.md", "--audience", "executive")
        s.check("explain: code for a business audience is counted and flagged",
                "I1.code_for_business_audience" in rules(rep), rules(rep))
        rc, rep = chk("biz_neg.md", "--audience", fx / "brief_ok.json")
        s.check("explain: --audience accepts a brief JSON and infers the audience from it",
                rep.get("audience", {}).get("role") == "executive" and "I1.business_no_decision" in rules(rep),
                rep.get("audience"))
        rc, rep = chk("mech_neg.md", *eng)
        s.check("explain: a mechanism-first opening is flagged (purpose before mechanism)",
                "I1.purpose_before_mechanism" in rules(rep), rules(rep))
        rc, rep = chk("mech_pos.md", *eng)
        s.check("explain: a purpose-first opening passes", rc == 0 and ok(rep), rules(rep))
        rc, rep = chk("throat.md", *eng)
        s.check("explain: a throat-clearing opener is flagged (lead with the answer)",
                "plain.lead_with_answer" in rules(rep), rules(rep))
        rc, rep = chk("child_long.md", "--audience", "child")
        rc2, rep2 = chk("child_short.md", "--audience", "child")
        s.check("explain: sentence length is COUNTED against the audience level's cap (child: long flagged, short "
                "passes)", "I1.sentences_too_long_for_audience" in rules(rep) and rc2 == 0 and ok(rep2),
                (rules(rep), rules(rep2)))

        # ============================================================ report only + exit codes
        target = fx / "concept_neg.md"
        h0, m0 = hashlib.sha256(target.read_bytes()).hexdigest(), os.stat(target).st_mtime_ns
        rc, rep = chk("concept_neg.md", "--shape", "concept", "--audience", "general-public",
                      "--out", str(d / "report.json"))
        h1, m1 = hashlib.sha256(target.read_bytes()).hexdigest(), os.stat(target).st_mtime_ns
        saved = json.loads((d / "report.json").read_text()) if (d / "report.json").exists() else {}
        s.check("explain: checker output is a report only (input byte-identical, mtime unchanged)",
                rc == 1 and h0 == h1 and m0 == m1 and rep.get("input_unchanged") and rep.get("input_sha256") == h0
                and saved.get("findings") == rep.get("findings"), (h0 == h1, m0 == m1))
        rc, rep = chk("concept_neg.md", "--out", str(fx / "concept_neg.md"))
        s.check("explain: --out refuses to overwrite the input", rc == 3
                and hashlib.sha256(target.read_bytes()).hexdigest() == h0, (rc, rep.get("summary")))
        rc_missing, rep_m = chk("does_not_exist.md")
        rc_bad, rep_b = chk("bad.json")
        rc_pass, _ = chk("end_pos.md", *eng)
        rc_rev, _ = chk("end_neg.md", *eng)
        s.check("explain: exit codes follow the gate protocol (0 pass, 1 review, 2 missing input, 3 malformed)",
                (rc_pass, rc_rev, rc_missing, rc_bad) == (0, 1, 2, 3), (rc_pass, rc_rev, rc_missing, rc_bad))
        rc, rep = chk("end_pos.md", *eng)
        s.check("explain: the report states what it does not judge (needs a reader)",
                any("false" in x for x in rep.get("not_judged", [])) and "not quality" in rep.get("summary", ""),
                rep.get("not_judged"))
        leftover = [p for p in fx.iterdir() if p.name not in FX and p.name not in ("brief_ok.json",
                                                                                  "brief_unclear.json")]
        s.check("explain: the checker writes nothing beside the input", not leftover, leftover)
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
