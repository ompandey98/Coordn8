#!/usr/bin/env python3
"""
Coordn8 test suite — zero third-party deps (no pytest required).

Covers the two things that protect the core guarantee:
  1. VERBATIM: every generated adapter embeds PLAYBOOK.md byte-for-byte, and the
     generator's --check agrees. This is what makes "never inferior to the raw
     prompt" mechanically true.
  2. GATES: each advisory gate returns the right exit code / status on crafted
     good and bad fixtures (a linear chain is flagged; a real tree passes; banned
     ids are caught; broken references are caught; missing inputs SKIP cleanly).

Run:  python3 tests/run_tests.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
PASS, FAIL = 0, 0
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL {name}  {detail}")


def run(script: str, args: list[str]):
    return subprocess.run([PY, str(ROOT / script), *args],
                          capture_output=True, text=True)


# ---------------------------------------------------------------------------
def test_verbatim():
    print("\n[verbatim: adapters embed the brain byte-for-byte]")
    brain = (ROOT / "engine" / "PLAYBOOK.md").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT / "build"))
    import generate_adapters as ga  # noqa

    planned = ga.build_all()
    for path, contents in planned.items():
        embedded = ga.extract_brain(contents)
        rel = path.relative_to(ROOT)
        check(f"{rel} embeds brain verbatim",
              embedded == brain,
              "embedded brain differs from PLAYBOOK.md")
        check(f"{rel} exists on disk and matches generator",
              path.exists() and path.read_text(encoding="utf-8") == contents,
              "on-disk adapter is stale — run generate_adapters.py")

    r = run("build/generate_adapters.py", ["--check"])
    check("generator --check passes", r.returncode == 0,
          r.stdout + r.stderr)


def test_tree_shape():
    print("\n[gate: tree_shape flags linear chains, passes real trees]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # a real decision tree: 2 branches, 3 terminals, labeled exits
        good = {
            "flows": [{
                "id": "u1", "title": "Refund", "expected_outcomes": 3,
                "nodes": [
                    {"id": "n1", "kind": "branch", "label": "Within 30 days?"},
                    {"id": "n2", "kind": "branch", "label": "Item damaged?"},
                    {"id": "t1", "kind": "terminal", "label": "Full refund + R1"},
                    {"id": "t2", "kind": "terminal", "label": "Store credit + R2"},
                    {"id": "t3", "kind": "terminal", "label": "Deny + R3"},
                ],
                "edges": [
                    {"from": "n1", "to": "n2", "when": "within 30 days"},
                    {"from": "n1", "to": "t3", "when": "past 30 days"},
                    {"from": "n2", "to": "t1", "when": "damaged"},
                    {"from": "n2", "to": "t2", "when": "not damaged"},
                ],
            }]
        }
        # a linear chain masquerading as a 3-outcome flow: 0 branches
        bad = {
            "flows": [{
                "id": "u2", "title": "Bad", "expected_outcomes": 3,
                "nodes": [
                    {"id": "a", "kind": "process", "label": "step 1"},
                    {"id": "b", "kind": "process", "label": "step 2"},
                    {"id": "c", "kind": "terminal", "label": "done"},
                ],
                "edges": [
                    {"from": "a", "to": "b", "when": ""},
                    {"from": "b", "to": "c", "when": ""},
                ],
            }]
        }
        gp = d / "good.json"; gp.write_text(json.dumps(good))
        bp = d / "bad.json"; bp.write_text(json.dumps(bad))

        r = run("scripts/gates/tree_shape.py", [str(gp)])
        check("real tree PASSES (exit 0)", r.returncode == 0, r.stdout)

        r = run("scripts/gates/tree_shape.py", [str(bp)])
        check("linear chain flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("linear-chain finding names 'linear chain'",
              "linear chain" in r.stdout.lower(), r.stdout)

        r = run("scripts/gates/tree_shape.py", [str(d / "missing.json")])
        check("missing flows -> SKIPPED (exit 2)", r.returncode == 2, r.stdout)


def test_content_ban():
    print("\n[gate: content_ban catches ids/emails, passes clean text]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        dirty = d / "dirty.html"
        dirty.write_text(
            "<p>Processed order #100294 for a customer.</p>\n"
            "<p>Contact alice@example.com about case-55321.</p>\n")
        clean = d / "clean.html"
        clean.write_text(
            "<h1>How the refund engine decides</h1>\n"
            "<p>The system routes each request by channel, then by age.</p>\n"
            "<p>This is a plain explanation with no identifiers at all.</p>\n"
            "<p>It uses machine learning for one judgment step only.</p>\n")

        r = run("scripts/gates/content_ban.py", [str(dirty)])
        check("dirty payload flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("catches order id", "100294" in r.stdout, r.stdout)
        check("catches email", "example.com" in r.stdout, r.stdout)

        r = run("scripts/gates/content_ban.py", [str(clean)])
        check("clean payload PASSES (exit 0)", r.returncode == 0, r.stdout)

        # capability-excuse text must never ship in a deliverable
        excuse = d / "excuse.html"
        excuse.write_text(
            "<h1>How it works</h1>\n"
            "<p>Note: video is not supported in this system.</p>\n")
        r = run("scripts/gates/content_ban.py", [str(excuse)])
        check("capability-excuse text flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it a capability_excuse",
              "capability_excuse" in r.stdout, r.stdout)

        # requester-personal framing must never ship — the requester is the
        # director, not the subject (PLAYBOOK 0.3a). This narration leaks the
        # requester's knowledge state, mood, and situation.
        personal = d / "personal.txt"
        personal.write_text(
            "Since you already know the basics, we'll skip them.\n"
            "This is the part you didn't understand in your repo.\n"
            "Don't worry if it gave you a headache last time.\n")
        r = run("scripts/gates/content_ban.py", [str(personal)])
        check("requester-personal framing flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it requester_personal_framing",
              "requester_personal_framing" in r.stdout, r.stdout)

        # generic teacherly "you" (addresses ANY reader) must NOT be flagged as
        # personal framing — only the requester's private context is banned.
        teacherly = d / "teacherly.txt"
        teacherly.write_text(
            "You'll see this pattern in the router module.\n"
            "Notice how each request travels through the cache.\n"
            "The system classifies, matches, then dispatches.\n")
        r = run("scripts/gates/content_ban.py", [str(teacherly)])
        check("generic teacherly 'you' PASSES (exit 0)",
              r.returncode == 0, r.stdout)

        # narration that reads a raw URL aloud is a transcription failure (2.2a):
        # the voice should speak meaning, the frame carries the literal.
        readaloud = d / "narration.txt"
        readaloud.write_text(
            "Open https://console.aws.amazon.com/s3/buckets/prod-data now.\n")
        r = run("scripts/gates/content_ban.py", [str(readaloud)])
        check("read-aloud raw URL flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it read_aloud_literal",
              "read_aloud_literal" in r.stdout, r.stdout)

        # audience role-play / greeting must never ship — the audience tailors
        # DESIGN, it is not a role the deliverable greets (PLAYBOOK 0.3 / 0.3a).
        # "Welcome to the team" is nonsensical in a file a hundred strangers share.
        greeting = d / "greeting.txt"
        greeting.write_text(
            "Welcome to the team! Let's look at the architecture.\n"
            "Now that you've joined, here's how requests flow.\n")
        r = run("scripts/gates/content_ban.py", [str(greeting)])
        check("audience greeting flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it audience_roleplay_greeting",
              "audience_roleplay_greeting" in r.stdout, r.stdout)

        # legit uses of the same words ('team' of services, a 'new' request)
        # must NOT be flagged as greetings — only the salutation is banned.
        not_greeting = d / "not_greeting.txt"
        not_greeting.write_text(
            "The team of services routes each new request.\n"
            "A new request enters the pipeline and is classified.\n")
        r = run("scripts/gates/content_ban.py", [str(not_greeting)])
        check("'team of services' / 'new request' PASSES (exit 0)",
              r.returncode == 0, r.stdout)

        # expanding an abbreviation is flagged so EACH full-form is verified
        # against a real source — a fabricated expansion is a factual defect
        # (PLAYBOOK 2.6). The gate surfaces the act of expanding; it can't know
        # the expansion is wrong, so it forces a check.
        acro = d / "acro.txt"
        acro.write_text(
            "TTL stands for time to live.\n"
            "It uses an LRU (Least Recently Used) cache.\n")
        r = run("scripts/gates/content_ban.py", [str(acro)])
        check("acronym expansion flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it acronym_expansion",
              "acronym_expansion" in r.stdout, r.stdout)

        # using an abbreviation WITHOUT expanding it is fine (keep-as-is rule)
        acro_ok = d / "acro_ok.txt"
        acro_ok.write_text(
            "The cache uses an LRU policy for eviction.\n"
            "Each request hits the API before the engine.\n")
        r = run("scripts/gates/content_ban.py", [str(acro_ok)])
        check("abbreviation used un-expanded PASSES (exit 0)",
              r.returncode == 0, r.stdout)

        # stale build/source metadata (comment markers, version stamps) must not
        # leak onto a frame or into narration (PLAYBOOK 2.1a "no meta chrome").
        stale = d / "stale.txt"
        stale.write_text(
            "# TODO: handle the retry path\n"
            "Running version 1.2.3 of the engine.\n")
        r = run("scripts/gates/content_ban.py", [str(stale)])
        check("stale meta (comment/version) flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names it stale_meta",
              "stale_meta" in r.stdout, r.stdout)


def test_facts_trace():
    print("\n[gate: facts_trace catches broken references]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "src").mkdir()
        code = d / "src" / "engine.py"
        code.write_text("\n".join(f"line {i}" for i in range(1, 51)))  # 50 lines
        manifest = {
            "repo_root": str(d),
            "claims": [
                {"id": "c1", "text": "handles routing", "ref": "src/engine.py:10"},
                {"id": "c2", "text": "bad line", "ref": "src/engine.py:999"},
                {"id": "c3", "text": "missing file", "ref": "src/nope.py:1"},
                {"id": "c4", "text": "no ref"},
            ],
        }
        mp = d / "claims.json"; mp.write_text(json.dumps(manifest))
        r = run("scripts/gates/facts_trace.py", [str(mp)])
        check("broken refs flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        try:
            payload = json.loads(r.stdout.splitlines()[0])
            check("exactly 3 unresolved (999, missing, no-ref)",
                  payload.get("unresolved") == 3, r.stdout)
        except Exception as e:  # noqa
            check("facts_trace emits parseable JSON", False, str(e))


def test_dom_smoke():
    print("\n[gate: dom_smoke catches external deps + missing markers]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        bad = d / "bad.html"
        bad.write_text(
            "<html><head>"
            "<link rel='stylesheet' href='https://cdn.example.com/x.css'>"
            "</head><body>" + ("<div class='tab'>x</div>" * 3) +
            ("padding " * 500) + "</body></html>")
        r = run("scripts/gates/dom_smoke.py",
                [str(bad), "--no-chrome", "--expect", "tab:3",
                 "nonexistent-marker:1"])
        # external dep + missing marker => REVIEW (or SKIPPED if no chrome but
        # still finds the external dep => REVIEW). Must not be PASS.
        check("external dep + missing marker not PASS",
              r.returncode in (1,), r.stdout)
        check("reports external dependency", "external depend" in r.stdout.lower(),
              r.stdout)


def test_layout_probe():
    print("\n[gate: layout_probe flags overflow/crowding/collision/tiny type]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        frame = {"width": 1920, "height": 1080}
        # clean: all inside the 5% safe area (96x54 margins), no overlap, big type
        good = {
            "frame": frame, "safe_margin_pct": 0.05,
            "samples": [{"scene": "s1", "t": 0.0, "boxes": [
                {"id": "title", "role": "title", "x": 120, "y": 80,
                 "w": 900, "h": 90, "font_px": 48, "text": True},
                {"id": "body", "role": "body", "x": 120, "y": 300,
                 "w": 800, "h": 200, "font_px": 30, "text": True},
            ]}]
        }
        # dirty: one box overflows the right edge, two texts overlap, one tiny font
        bad = {
            "frame": frame, "safe_margin_pct": 0.05,
            "samples": [{"scene": "s2", "t": 0.5, "boxes": [
                {"id": "spill", "x": 1700, "y": 100, "w": 400, "h": 80,
                 "font_px": 40, "text": True},          # x2=2100 > 1920 -> overflow
                {"id": "a", "x": 200, "y": 500, "w": 300, "h": 100,
                 "font_px": 30, "text": True},
                {"id": "b", "x": 350, "y": 550, "w": 300, "h": 100,
                 "font_px": 20, "text": True},          # overlaps 'a' AND tiny type
            ]}]
        }
        gp = d / "good.json"; gp.write_text(json.dumps(good))
        bp = d / "bad.json"; bp.write_text(json.dumps(bad))

        r = run("scripts/gates/layout_probe.py", [str(gp)])
        check("clean layout PASSES (exit 0)", r.returncode == 0, r.stdout)

        r = run("scripts/gates/layout_probe.py", [str(bp)])
        check("bad layout flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("names the overflow", "overflow" in r.stdout.lower(), r.stdout)
        check("names the overlap/collision", "overlap" in r.stdout.lower(), r.stdout)
        check("names the tiny type", "too small" in r.stdout.lower()
              or "floor" in r.stdout.lower(), r.stdout)

        r = run("scripts/gates/layout_probe.py", [str(d / "missing.json")])
        check("missing layout -> SKIPPED (exit 2)", r.returncode == 2, r.stdout)


def test_visual_density():
    print("\n[gate: visual_density flags sparse/static/filler/generic plans]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # --- VIDEO storyboard ---------------------------------------------
        # rich scene: non-text primary, 2 explained visuals, real motion,
        # full-frame regions.
        good_sb = {
            "fps": 30, "frame": {"width": 1920, "height": 1080},
            "scenes": [{
                "id": "s1", "beat": "machinery",
                "primary_visual": "request traveling the router -> cache -> engine",
                "elements": [
                    {"id": "router", "kind": "component",
                     "explains": "entry point that classifies the request",
                     "ref": "router.py:40", "region": "left-third",
                     "motion": "pulse when the token arrives"},
                    {"id": "token", "kind": "data",
                     "explains": "one request moving through the system",
                     "ref": "types.py:12", "region": "center",
                     "motion": "travels left->right, eased"},
                    {"id": "cache", "kind": "component",
                     "explains": "slots that fill and evict",
                     "ref": "cache.py:88", "region": "right-third"},
                ],
                "moving_element_count": 2,
                "uses_regions": ["left-third", "center", "right-third"],
            }]
        }
        # bad scene: text-only primary, no motion, center-only, a filler star
        # with no explains.
        bad_sb = {
            "fps": 30, "frame": {"width": 1920, "height": 1080},
            "scenes": [{
                "id": "s2", "beat": "intro",
                "primary_visual": "a text box",
                "elements": [
                    {"id": "sparkle", "kind": "star", "region": "center"},
                ],
                "moving_element_count": 0,
                "uses_regions": ["center"],
            }]
        }
        gsb = d / "good_sb.json"; gsb.write_text(json.dumps(good_sb))
        bsb = d / "bad_sb.json"; bsb.write_text(json.dumps(bad_sb))

        r = run("scripts/gates/visual_density.py", [str(gsb)])
        check("rich storyboard PASSES (exit 0)", r.returncode == 0, r.stdout)
        check("auto-detects video kind", '"kind": "video"' in r.stdout, r.stdout)

        r = run("scripts/gates/visual_density.py", [str(bsb)])
        check("thin storyboard flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        check("names the text-only primary", "show, don't tell" in r.stdout.lower()
              or "non-text primary" in r.stdout.lower(), r.stdout)
        check("names the missing motion", "static hold" in r.stdout.lower()
              or "no motion" in r.stdout.lower(), r.stdout)
        check("names the center-only composition", "center" in r.stdout.lower(),
              r.stdout)
        check("flags the decorative star", "decorative" in r.stdout.lower()
              or "banned" in r.stdout.lower(), r.stdout)

        # --- PAGE plan -----------------------------------------------------
        good_pp = {
            "sections": [{
                "id": "architecture", "idea": "how data flows",
                "primary_visual": "animated component map with particle flows",
                "elements": [
                    {"id": "map", "kind": "diagram",
                     "explains": "every component and its edges",
                     "ref": "app.py:1", "region": "full"},
                    {"id": "legend", "kind": "figure",
                     "explains": "color = source of truth", "ref": "app.py:2"},
                ],
                "interaction": "hover a component to see its config",
                "uses_full_width": True,
            }]
        }
        bad_pp = {
            "sections": [{
                "id": "overview", "idea": "what it is",
                "primary_visual": "bullet list",
                "elements": [
                    {"id": "b1", "kind": "box", "explains": "point one",
                     "ref": "x:1"},
                ],
                "uses_full_width": False,
            }]
        }
        gpp = d / "good_pp.json"; gpp.write_text(json.dumps(good_pp))
        bpp = d / "bad_pp.json"; bpp.write_text(json.dumps(bad_pp))

        r = run("scripts/gates/visual_density.py", [str(gpp), "--kind", "page"])
        check("rich page plan PASSES (exit 0)", r.returncode == 0, r.stdout)

        r = run("scripts/gates/visual_density.py", [str(bpp), "--kind", "page"])
        check("thin page section flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names the missing interaction", "interaction" in r.stdout.lower(),
              r.stdout)
        check("names the wasted width", "full width" in r.stdout.lower()
              or "center strip" in r.stdout.lower(), r.stdout)

        # a section that self-declares it's a re-skinned video beat is flagged
        # (the page must be a reader, not the video's timeline replayed — 1.1a)
        reskin_pp = {
            "sections": [{
                "id": "machinery", "idea": "the engine",
                "primary_visual": "animated pipeline diagram",
                "elements": [
                    {"id": "pipe", "kind": "diagram",
                     "explains": "the stages end to end", "ref": "e.py:1"},
                    {"id": "note", "kind": "figure",
                     "explains": "stage timings", "ref": "e.py:9"},
                ],
                "interaction": "step through each stage",
                "uses_full_width": True,
                "not_video_scene": False,   # <- admits it mirrors a video beat
            }]
        }
        rpp = d / "reskin_pp.json"; rpp.write_text(json.dumps(reskin_pp))
        r = run("scripts/gates/visual_density.py", [str(rpp), "--kind", "page"])
        check("re-skinned video beat flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        check("names the re-skin / reader-destination failure",
              "re-skinned" in r.stdout.lower() or "reader destination" in r.stdout.lower()
              or "own information architecture" in r.stdout.lower(), r.stdout)

        # the optional video-embed "Watch" tab is EXEMPT — it carries no visuals
        # of its own and must not be flagged as sparse (1.1 item 6)
        embed_pp = {
            "sections": [
                {"id": "arch", "idea": "data flow",
                 "primary_visual": "animated component map",
                 "elements": [
                     {"id": "map", "kind": "diagram", "explains": "components",
                      "ref": "a.py:1"},
                     {"id": "legend", "kind": "figure", "explains": "colors",
                      "ref": "a.py:2"}],
                 "interaction": "hover", "uses_full_width": True},
                {"id": "watch", "role": "embed",
                 "primary_visual": "embedded video player with speed controls",
                 "elements": []},
            ]
        }
        epp = d / "embed_pp.json"; epp.write_text(json.dumps(embed_pp))
        r = run("scripts/gates/visual_density.py", [str(epp), "--kind", "page"])
        check("page with an exempt video-embed tab PASSES (exit 0)",
              r.returncode == 0, r.stdout)
        check("marks the embed tab exempt", "exempt" in r.stdout.lower(), r.stdout)

        # missing plan -> SKIPPED (build the plan before rendering)
        r = run("scripts/gates/visual_density.py", [str(d / "missing.json")])
        check("missing plan -> SKIPPED (exit 2)", r.returncode == 2, r.stdout)


def test_verify_manifest():
    print("\n[gate: verify_manifest refuses overclaims, authorizes only what passed]")
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # overclaim: claims 'explainability' but that item FAILED. The gate must
        # refuse that claim word and authorize only the genuinely-passing ones.
        bad = {
            "deliverable": "video",
            "claims": ["sync", "animation", "explainability"],
            "items": [
                {"id": "beat_sync", "status": "pass",
                 "evidence": "42/42 rows matched [verified-live]", "claim": "sync"},
                {"id": "layout", "status": "pass",
                 "evidence": "0 overflow across 30 samples", "claim": "animation"},
                {"id": "explainability", "status": "fail",
                 "evidence": "scene 4 told-not-shown", "claim": "explainability",
                 "risk": "scene 4 needs a better visual"},
            ],
        }
        bp = d / "bad.json"; bp.write_text(json.dumps(bad))
        r = run("scripts/gates/verify_manifest.py", [str(bp)])
        check("overclaim flagged REVIEW (exit 1)", r.returncode == 1, r.stdout)
        payload = json.loads(r.stdout.splitlines()[0])
        check("refuses the unbacked claim 'explainability'",
              payload.get("overclaimed") == ["explainability"], r.stdout)
        check("authorizes ONLY the passing claims",
              payload.get("authorized_claim") == "Verified: sync, animation",
              r.stdout)
        check("surfaces the recorded risk (not swept away)",
              any(x.get("risk") for x in payload.get("risks_and_assumptions", [])),
              r.stdout)

        # ready: a page build where every required item passes with evidence and
        # every claim word is backed -> PASS, full claim authorized.
        good = {
            "deliverable": "page",
            "claims": ["facts", "trees", "shareability", "explainability"],
            "items": [
                {"id": "facts_trace", "status": "pass", "claim": "facts",
                 "evidence": "40/40 refs resolve [code-traced]"},
                {"id": "tree_shape", "status": "pass", "claim": "trees",
                 "evidence": "3 flows, all branchy [verified-live]"},
                {"id": "dom_smoke", "status": "pass", "evidence": "5 tabs, 0 deps"},
                {"id": "content_ban", "status": "pass", "evidence": "0 hits"},
                {"id": "visual_density", "status": "pass", "evidence": "avg 3.2"},
                {"id": "shareability", "status": "pass", "claim": "shareability",
                 "evidence": "critic: no personal framing"},
                {"id": "explainability", "status": "pass", "claim": "explainability",
                 "evidence": "critic read cold: every section clicks"},
                {"id": "adversarial", "status": "pass", "evidence": "12 re-verified"},
            ],
        }
        gp = d / "good.json"; gp.write_text(json.dumps(good))
        r = run("scripts/gates/verify_manifest.py", [str(gp)])
        check("fully-verified manifest PASSES (exit 0)", r.returncode == 0, r.stdout)
        payload = json.loads(r.stdout.splitlines()[0])
        check("authorizes the full claim when all pass",
              payload.get("authorized_claim") ==
              "Verified: facts, trees, shareability, explainability", r.stdout)

        # a 'pass' with NO evidence is not a pass — it must be downgraded and the
        # claim it backs refused (honesty: no claim without matching evidence).
        noev = {"deliverable": "page", "claims": ["facts"],
                "items": [{"id": "facts_trace", "status": "pass", "claim": "facts"}]}
        np = d / "noev.json"; np.write_text(json.dumps(noev))
        r = run("scripts/gates/verify_manifest.py", [str(np)])
        check("pass-without-evidence flagged REVIEW (exit 1)",
              r.returncode == 1, r.stdout)
        payload = json.loads(r.stdout.splitlines()[0])
        check("refuses to authorize an evidence-less pass",
              payload.get("authorized_claim") == "", r.stdout)
        check("names the missing-evidence problem",
              any("NO evidence" in f for f in payload.get("findings", [])),
              r.stdout)

        # missing manifest -> SKIPPED (build must produce one before any claim)
        r = run("scripts/gates/verify_manifest.py", [str(d / "missing.json")])
        check("missing manifest -> SKIPPED (exit 2)", r.returncode == 2, r.stdout)


def test_setup_env():
    print("\n[setup_env: dry-run plans installs and never claims false success]")
    r = run("scripts/setup_env.py", ["--dry-run", "--json"])
    check("setup_env dry-run runs (exit 0)", r.returncode == 0, r.stderr)
    try:
        data = json.loads(r.stdout)
        check("dry_run flag is true", data.get("dry_run") is True, r.stdout)
        check("reports capabilities_after", "capabilities_after" in data, r.stdout)
        check("reports video_ready boolean",
              isinstance(data.get("video_ready"), bool), r.stdout)
        # dry-run must not actually install: every action is a plan, not a result
        acts = data.get("actions", [])
        statuses = {a.get("status") for a in acts}
        check("dry-run installs nothing (no 'installed' status)",
              "installed" not in statuses, str(statuses))
        # honesty: if video isn't ready, blockers must be listed specifically
        if not data.get("video_ready"):
            check("names specific blockers when not video-ready",
                  len(data.get("still_blocking_video", [])) > 0, r.stdout)
        else:
            check("names specific blockers when not video-ready", True, "")
    except Exception as e:  # noqa
        check("setup_env emits valid JSON", False, str(e))

    # subset flag is accepted
    r = run("scripts/setup_env.py", ["--dry-run", "--json", "--only", "pillow"])
    check("setup_env --only subset runs (exit 0)", r.returncode == 0, r.stderr)


def test_env_detect():
    print("\n[env_detect: emits a valid tier + bounded worker budget]")
    r = run("scripts/env_detect.py", ["--json"])
    check("env_detect runs (exit 0)", r.returncode == 0, r.stderr)
    try:
        data = json.loads(r.stdout)
        check("tier in A/B/C/D", data.get("tier") in ("A", "B", "C", "D"),
              str(data.get("tier")))
        w = data.get("parallel_budget", {}).get("cpu_bound_workers", 0)
        check("cpu workers bounded >=1 and <= cores",
              1 <= w <= (data.get("machine", {}).get("cpu_cores") or 1),
              f"workers={w}")
    except Exception as e:  # noqa
        check("env_detect emits valid JSON", False, str(e))


def test_parallel_pool():
    print("\n[render: bounded parallel pool self-test]")
    r = run("scripts/render/parallel.py", ["--selftest"])
    check("parallel pool self-test passes", r.returncode == 0,
          r.stdout + r.stderr)


def main() -> int:
    print("Coordn8 test suite")
    print("=" * 50)
    test_verbatim()
    test_tree_shape()
    test_content_ban()
    test_facts_trace()
    test_dom_smoke()
    test_layout_probe()
    test_visual_density()
    test_verify_manifest()
    test_setup_env()
    test_env_detect()
    test_parallel_pool()
    print("=" * 50)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nFailures:")
        for f in FAILURES:
            print("  - " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
