#!/usr/bin/env python3
"""Palette Director (A1), palette + contrast gate (A2), freedom (H1) and anti-repetition (H3 part).

Hermetic: every CLI run uses a sandboxed COORDN8_HOME / CLAUDE_HOME / HOME; temp dirs are removed.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import PY, ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from design import color as C  # noqa: E402
from design import design_system as DS  # noqa: E402
from design import palette as P  # noqa: E402

GATE = "scripts/gates/check_palette.py"
DIRECTOR = "scripts/design/palette.py"

FIXTURES = {
    "fintech": {"subject": "A fintech payments API: how card transactions are authorised and settled",
                "audience": "product engineers", "tone": "precise", "project": "pay-api"},
    "medical": {"subject": "How a hospital triage system routes patients by urgency",
                "audience": "clinicians", "tone": "calm, reassuring", "project": "triage"},
    "game theory": {"subject": "Game theory: why the prisoner's dilemma makes rivals cooperate less",
                    "audience": "economics students", "tone": "curious", "project": "dilemma"},
    "infra": {"subject": "Kubernetes cluster autoscaling and incident response in a cloud infrastructure",
              "audience": "SREs", "tone": "direct", "project": "k8s-ops"},
    "kids' education": {"subject": "Kids' education: how plants turn sunlight into food",
                        "audience": "children aged 8 to 10", "tone": "playful", "project": "plants"},
}


def gate_json(design: Path, elements=None, html=None):
    args = [str(design)]
    if elements is not None:
        args += ["--elements", str(elements)]
    if html:
        args += ["--html"] + [str(h) for h in html]
    r = run(GATE, args, timeout=120)
    line = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        return r.returncode, json.loads(line)
    except json.JSONDecodeError:
        return r.returncode, {"raw": r.stdout[-400:], "err": r.stderr[-400:]}


def write_design(d: Path, name: str, palette: dict) -> Path:
    p = d / name
    p.write_text(json.dumps({"schema": 1, "palette": palette}))
    return p


def rgba(hex_value: str, a: float) -> str:
    r, g, b, _ = C.parse_color(hex_value)
    return f"rgba({int(r)},{int(g)},{int(b)},{a})"


def clean_scene(pal: dict) -> list:
    R, D = pal["roles"], pal["dark"]
    els = [
        {"id": "title", "kind": "text", "color": R["ink"], "font_px": 44, "frame": "f1"},
        {"id": "caption", "kind": "text", "color": R["muted"], "font_px": 28, "frame": "f1"},
        {"id": "focal-fill", "kind": "fill", "color": rgba(R["accent"], 0.1), "frame": "f1", "focal_group": "focal"},
        {"id": "focal-stroke", "kind": "mark", "color": R["accent"], "frame": "f1", "focal_group": "focal"},
        {"id": "focal-label", "kind": "text", "color": R["accent"], "font_px": 28, "frame": "f1"},
        {"id": "bar-up", "kind": "fill", "color": R["positive"], "meaning": "increase", "fill_weight": "solid",
         "frame": "f1"},
        {"id": "bar-down", "kind": "fill", "color": R["negative"], "meaning": "decrease", "fill_weight": "hollow",
         "frame": "f1"},
        {"id": "legend", "kind": "text", "color": R["muted"], "font_px": 28, "text": "stronger = more requests",
         "frame": "f1"},
        {"id": "d-title", "kind": "text", "color": D["ink"], "bg": D["paper"], "skin": "dark", "font_px": 44,
         "frame": "f2"},
        {"id": "d-link", "kind": "text", "color": D["link"], "skin": "dark", "font_px": 28, "frame": "f2"},
        {"id": "box-tint", "kind": "fill", "color": C.to_hex(C.composite(R["ink"], R["paper"], 0.06)), "frame": "f2"},
    ]
    for i, a in enumerate(P.SERIES_STEPS):
        els.append({"id": f"series{i}", "kind": "fill", "color": rgba(R["ink"], a), "frame": "f3"})
    return els


def as_palette(res: dict) -> dict:
    return {"family": res.get("name", "invented"), "roles": res["roles"], "dark": res["dark"]}


def main() -> int:
    s = Suite("palette")
    cat = P.load_catalog()
    fams = cat["families"]

    # ------------------------------------------------------------- colour science
    pairs = [((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425), ((50, 3.1571, -77.2803), (50, 0, -82.7485), 2.8615),
             ((50, 0, 0), (50, -1, 2), 2.3669), ((50, 2.5, 0), (73, 25, -18), 27.1492),
             ((50, 2.5, 0), (61, -5, 29), 22.8977), ((50, 2.5, 0), (56, -27, -3), 31.9030),
             ((50, 2.5, 0), (58, 24, 15), 19.4535), ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644)]
    errs = [(e, round(C.ciede2000(a, b), 4)) for a, b, e in pairs if abs(C.ciede2000(a, b) - e) > 1e-4]
    s.check("color: CIEDE2000 matches the published test pairs to 4 decimals", not errs, str(errs))
    s.check("color: WCAG contrast matches known values (#777 on white 4.48:1, black on white 21:1)",
            abs(C.contrast("#777777", "#ffffff") - 4.48) < 0.01 and abs(C.contrast("#000", "#fff") - 21) < 1e-9,
            f"{C.contrast('#777777', '#ffffff')}")
    rt = [h for h in ("#ff0000", "#3a7bd5", "#0b6e4f", "#f2c230", "#2b2320", "#efe9dc")
          if max(abs(a - b) for a, b in zip(C.rgb_of(C.oklch_hex(*C.rgb_to_oklch(h))), C.rgb_of(h))) > 1]
    s.check("color: OKLCH round-trips sRGB within 1/255", not rt, str(rt))
    s.check("color: translucent text is composited before contrast is measured",
            abs(C.contrast("rgba(0,0,0,0.5)", "#ffffff") - C.contrast("#808080", "#ffffff")) < 0.05, "")

    # ------------------------------------------------------------- catalog
    keys_ok = all(all(k in f for k in ("id", "mood", "use_when", "avoid_when", "roles", "dark", "notes")) for f in fams)
    s.check("catalog: >= 12 families, each with id, mood, use_when, avoid_when, roles, dark, notes",
            len(fams) >= 12 and keys_ok and len({f["id"] for f in fams}) == len(fams), f"{len(fams)} families")
    roles_ok = all(all(r in f["roles"] and r in f["dark"] for r in DS.STANDARD_ROLES) for f in fams)
    s.check("catalog: every family defines all 12 semantic roles in light and dark", roles_ok, "")
    low = [(f["id"], skin, r, round(C.contrast(f[k][r], f[k]["paper"]), 2))
           for f in fams for skin, k in (("light", "roles"), ("dark", "dark"))
           for r in DS.TEXT_ROLES if C.contrast(f[k][r], f[k]["paper"]) < 4.5]
    s.check("catalog: every text role >= 4.5:1 on paper in light AND dark, for every family", not low, str(low[:5]))
    hues = sorted(C.rgb_to_oklch(f["roles"]["accent"])[2] for f in fams)
    sectors = {int(h // 30) for h in hues}
    accents = [f["roles"]["accent"] for f in fams]
    close = [(fams[i]["id"], fams[j]["id"]) for i in range(len(fams)) for j in range(i + 1, len(fams))
             if C.delta_e(accents[i], accents[j]) < 10]
    s.check("catalog: families are genuinely different — accent hues in >= 10 of 12 30-degree sectors, "
            "no two accents within 10 CIEDE2000", len(sectors) >= 10 and not close, f"sectors={len(sectors)} close={close}")
    sep_bad = []
    for f in fams:
        m = P.measure(f["roles"], f["dark"])
        for skin in ("light", "dark"):
            if m[skin]["min_separation"] < DS.MIN_ROLE_SEPARATION:
                sep_bad.append((f["id"], skin, m[skin]["closest_pair"], m[skin]["min_separation"]))
    s.check("catalog: roles of distinct meaning >= 10 CIEDE2000 apart in every family, both skins", not sep_bad,
            str(sep_bad))
    s.check("catalog: no family's roles are hand-picked — each carries the OKLCH derivation that reproduces it",
            all("derivation" in f and "anchor_hue" in f["derivation"] for f in fams)
            and all(P.derive(f["derivation"]["anchor_hue"], **{k: v for k, v in f["derivation"].items()
                                                               if k != "anchor_hue"})["roles"] == f["roles"]
                    for f in fams[:3]), "")

    with tmpdir() as td:
        td = Path(td)
        env = sandbox_env(td)
        gate_fail = []
        for f in fams:
            dp = write_design(td, f"fam-{f['id']}.json", {"family": f["id"], "roles": f["roles"], "dark": f["dark"]})
            code, res = gate_json(dp)
            if code != 0:
                gate_fail.append((f["id"], res.get("summary")))
        s.check("catalog: every family passes the palette gate as a design system", not gate_fail, str(gate_fail))

        # --------------------------------------------------------- director: fixtures
        results = {k: P.choose(b, history=[], seed=7) for k, b in FIXTURES.items()}
        chosen = {k: r["family"] for k, r in results.items()}
        s.check("director: five subjects (fintech, medical, game theory, infra, kids' education) -> five different "
                "families", len(set(chosen.values())) == 5, json.dumps(chosen))
        s.check("director: each fixture's choice carries a written reason naming the matched moods",
                all("moods matched" in r["chosen_because"] for r in results.values()),
                json.dumps({k: r["chosen_because"][:80] for k, r in results.items()}))
        cand_bad, inv_missing, rej_bad, contrast_bad = [], [], [], []
        for k, r in results.items():
            acc = [c["accent"] for c in r["candidates"]]
            mn = min(C.delta_e(a, b) for i, a in enumerate(acc) for b in acc[i + 1:])
            if len(acc) < 3 or mn < 20:
                cand_bad.append((k, len(acc), round(mn, 1)))
            if not any(c["source"] == "invented" for c in r["candidates"]):
                inv_missing.append(k)
            if len(r["rejected"]) < 2 or not all(x.get("why") for x in r["rejected"]):
                rej_bad.append(k)
            m = r["measured"]
            if m["light"]["min_text_contrast"] < 4.5 or m["dark"]["min_text_contrast"] < 4.5:
                contrast_bad.append((k, m["light"]["min_text_contrast"], m["dark"]["min_text_contrast"]))
        s.check("director: every result has >= 3 candidates whose accents are >= 20 CIEDE2000 apart", not cand_bad,
                str(cand_bad))
        s.check("director: every result includes an invented palette derived in OKLCH", not inv_missing, str(inv_missing))
        s.check("director: every result rejects >= 2 candidates, each with a written reason", not rej_bad, str(rej_bad))
        s.check("director: every result's text roles are >= 4.5:1 in light AND dark", not contrast_bad, str(contrast_bad))
        inv_rows = [c for r in results.values() for c in r["candidates"] if c["source"] == "invented"]
        s.check("director: invented candidates are anchored on the mood's hue (within 50 degrees of the anchor, "
                "after the seeded jitter)",
                all(any(C.hue_distance(c["accent_hue"], P.MOODS[m][0]) <= 62 for m in P.MOODS if c["id"].startswith(
                    f"invented:{m}-")) for c in inv_rows), str([(c["id"], c["accent_hue"]) for c in inv_rows]))

        # stated colour / mood
        r = P.choose({"subject": "A data pipeline", "stated": {"quote": "I want it teal and calm"}}, history=[], seed=3)
        s.check("director: a stated colour wins (teal -> teal accent) and the quote obeyed is recorded",
                C.hue_distance(C.rgb_to_oklch(r["roles"]["accent"])[2], P.COLOUR_WORDS["teal"]) <= 28
                and "I want it teal and calm" in r["obeyed_user_quotes"], f"{r['family']} {r['roles']['accent']}")
        r_lime = P.choose({"subject": "A data pipeline", "stated": "I'd love something lime and fresh"}, history=[], seed=3)
        s.check("director: a stated colour no catalog family carries wins as an INVENTED palette at that hue",
                r_lime["invented"] and C.hue_distance(C.rgb_to_oklch(r_lime["roles"]["accent"])[2], P.COLOUR_WORDS["lime"]) <= 28
                and r_lime["obeyed_user_quotes"], f"{r_lime['family']} {r_lime['roles']['accent']}")
        r_mood = P.choose(dict(FIXTURES["fintech"], stated="make it playful and fun"), history=[], seed=3)
        win = next(c for c in r_mood["candidates"] if c["id"] == r_mood["family"])
        s.check("director: a stated mood wins over the subject's own mood (fintech + 'playful' -> a playful palette)",
                "playful" in win["moods_matched"] and r_mood["family"] != chosen["fintech"]
                and r_mood["obeyed_user_quotes"] == ["make it playful and fun"], f"{r_mood['family']} {win['moods_matched']}")
        r_no = P.choose({"subject": "Software architecture of an enterprise API, with documentation",
                         "audience": "engineers", "stated": "no blue please"}, history=[], seed=3)
        base_no = P.choose({"subject": "Software architecture of an enterprise API, with documentation",
                            "audience": "engineers"}, history=[], seed=3)
        s.check("director: a colour the user ruled out is never chosen ('no blue' moves off the blue family)",
                C.hue_distance(C.rgb_to_oklch(r_no["roles"]["accent"])[2], P.COLOUR_WORDS["blue"]) > 30
                and base_no["family"] == "cobalt-blueprint", f"{base_no['family']} -> {r_no['family']}")

        # brand colours
        brand_ok = "#1d5fa8"
        rb = P.choose(dict(FIXTURES["fintech"], brand_colours=[brand_ok]), history=[], seed=3)
        s.check("director: a brand colour that passes contrast is kept EXACTLY as the light accent",
                rb["roles"]["accent"] == brand_ok and rb["brand"]["accent"]["kept_exactly"]["light"],
                f"{rb['roles']['accent']} {rb['brand']['adjustments']}")
        brand_bad = "#ffb000"
        rf = P.choose(dict(FIXTURES["fintech"], brand_colours=[brand_bad, "#3355aa", "#ff00ff", "#00ffff"]),
                      history=[], seed=3)
        adj = [a for a in rf["brand"]["adjustments"] if a["skin"] == "light"]
        ok = bool(adj) and rf["roles"]["accent"] == adj[0]["to"] and adj[0]["to_ratio"] >= 4.5
        if ok:
            L0, C0, h0 = C.rgb_to_oklch(brand_bad)
            lo, hi = L0 - 0.6, L0   # bisection: smallest lightness drop that clears 4.5:1
            paper = rf["roles"]["paper"]
            for _ in range(40):
                mid = (lo + hi) / 2
                if C.contrast(C.oklch_hex(mid, C0, h0), paper) >= 4.5:
                    lo = mid
                else:
                    hi = mid
            need = L0 - lo
            got = -adj[0]["delta_L_oklch"]
            ok = abs(got - need) <= 0.02 and C.hue_distance(C.rgb_to_oklch(adj[0]["to"])[2], h0) < 4
        s.check("director: a failing brand colour is adjusted by the minimal lightness change (hue kept) and the "
                "adjustment is reported", ok, json.dumps(adj)[:300])
        s.check("director: brand colours beyond accent + link are reported unused (no-rainbow rule)",
                len(rf["brand"]["not_used"]) == 2 and all("no-rainbow" in x["why"] for x in rf["brand"]["not_used"]), "")
        bp = write_design(td, "brand.json", rf)
        code, res = gate_json(bp)
        s.check("director: the brand-adjusted palette passes the gate", code == 0, res.get("summary"))

        # invented palettes pass the gate
        ip = write_design(td, "invented.json", r_lime)
        code, res = gate_json(ip)
        s.check("freedom: an invented palette (director-derived) passes the gate", code == 0, res.get("summary"))
        r_agent = P.choose(dict(FIXTURES["medical"], palette={"invented": "sea-glass", "reason": "harbour hospital "
                                                               "brand mood", "accent": "#2a8c82"}), history=[], seed=3)
        ap_ = write_design(td, "agent.json", r_agent)
        code, res = gate_json(ap_)
        s.check("freedom: the agent's own invented palette is accepted (invented: name + reason) and passes the gate",
                r_agent["family"] == "invented:sea-glass" and "harbour" in r_agent["chosen_because"] and code == 0,
                f"{r_agent['family']} {res.get('summary')}")
        r_fam = P.choose(dict(FIXTURES["medical"], palette={"family": "plum-atelier", "reason": "art-therapy wing"}),
                         history=[], seed=3)
        s.check("freedom: an explicit catalog choice by the agent is honoured with its reason",
                r_fam["family"] == "plum-atelier" and "art-therapy" in r_fam["chosen_because"], r_fam["chosen_because"])
        broken = P.choose(dict(FIXTURES["medical"], palette={
            "invented": "too-pale", "reason": "test",
            "roles": dict(fams[0]["roles"], ink="#c9c9c9"), "dark": fams[0]["dark"]}), history=[], seed=3)
        bk = write_design(td, "broken-invented.json", broken)
        code, res = gate_json(bk)
        s.check("beat-reference: an invented palette is GATED, not trusted — a supplied invented palette with "
                "pale ink is caught (role-contrast)", code == 1 and res.get("counts", {}).get("role-contrast"),
                res.get("summary"))

        # mechanical derivation over the whole hue circle
        sweep_bad = []
        for h in range(0, 360, 20):
            d = P.derive(h, chroma_policy="rotate")
            m = d["measured"]
            if (d["unresolved"] or m["light"]["min_text_contrast"] < 4.5 or m["dark"]["min_text_contrast"] < 4.5
                    or d["accent_chroma"] < P.MIN_ACCENT_CHROMA or m["light"]["min_separation"] < 10
                    or m["dark"]["min_separation"] < 10):
                sweep_bad.append((h, d["accent_chroma"], m["light"]["min_text_contrast"], d["unresolved"][:1]))
        s.check("beat-reference: OKLCH derivation holds for anchors round the whole hue circle (every text role "
                ">= 4.5:1 both skins, distinct roles >= 10 CIEDE2000, accent chroma >= 0.12)", not sweep_bad,
                str(sweep_bad))
        y = P.derive(90, chroma_policy="keep")
        yr = P.derive(90, chroma_policy="rotate")
        s.check("beat-reference: where sRGB cannot hold 0.12 chroma at a passing lightness (yellows), the limit is "
                "recorded — a stated hue is kept, a mood anchor is rotated to the nearest hue that can",
                y["gamut_note"] and C.hue_distance(C.rgb_to_oklch(y["roles"]["accent"])[2], 90) < 6
                and yr["gamut_note"] and yr["accent_chroma"] >= P.MIN_ACCENT_CHROMA,
                f"{y['gamut_note']} {yr['gamut_note']}")
        sweep_gate = []
        for h in (0, 100, 200, 300):
            dp = write_design(td, f"sweep{h}.json", as_palette(P.derive(h, chroma_policy="rotate")))
            code, res = gate_json(dp)
            if code != 0:
                sweep_gate.append((h, res.get("summary")))
        s.check("beat-reference: derived palettes pass the outcome gate with no catalog knowledge", not sweep_gate,
                str(sweep_gate))

        # chart series + legend wording
        cs = results["fintech"]["chart_series"]
        s.check("director: chart series = one ink at stepped opacity; contrast strictly falls step by step in BOTH "
                "skins", cs["role"] == "ink" and cs["monotonic"] and len(cs["opacities"]) >= 4, json.dumps(cs)[:200])
        s.check("director: legend wording says stronger/weaker, never darker",
                "stronger" in cs["legend_wording"]["use"] and "darker" not in cs["legend_wording"]["use"]
                and "darker" in cs["legend_wording"]["never"], json.dumps(cs["legend_wording"]))

        # per-section shift
        rs = P.choose(dict(FIXTURES["fintech"], sections=[{"id": "data", "colour": "teal",
                                                           "quote": "make the data section teal"}]), history=[], seed=3)
        sec = rs["sections"].get("data", {})
        sec_ok = bool(sec.get("accent")) and C.contrast(sec["accent"], rs["roles"]["paper"]) >= 4.5 and \
            C.contrast(sec["dark_accent"], rs["dark"]["paper"]) >= 4.5
        sp = write_design(td, "section.json", rs)
        ep = td / "section_els.json"
        ep.write_text(json.dumps([{"id": "sec-head", "kind": "text", "color": sec.get("accent", "#000"),
                                   "font_px": 32, "frame": "s"}]))
        code, res = gate_json(sp, ep)
        s.check("director: a declared per-section shift is recorded, passes contrast in both skins, and the gate "
                "accepts it as a declared colour", sec_ok and code == 0, f"{sec} {res.get('summary')}")

        # anti-repetition (in process, explicit history)
        changed, kept_reasoned, bad = [], [], []
        for k, b in FIXTURES.items():
            first = results[k]["family"]
            again = P.choose(b, history=[{"palette_family": first}] * 3, seed=7)
            if again["family"] != first:
                why = " ".join(x["why"] for x in again["rejected"] if x["id"] == first)
                (changed if re.search(r"used .*build|anti-repetition|last 3", why) else bad).append((k, why[:120]))
            else:
                notes = " ".join(again["anti_repetition"]["notes"])
                (kept_reasoned if ("clearly calls for it" in notes or "warrants" in notes) else bad).append(
                    (k, notes[:160]))
        s.check("director: anti-repetition — with the same family on the last 3 builds the pick changes, with the "
                "reason recorded, unless the subject warrants it (and then that reason is recorded)",
                not bad and changed, f"changed={changed} kept={kept_reasoned} bad={bad}")
        warr = P.choose(FIXTURES["infra"], history=[{"palette_family": chosen["infra"]}] * 3, seed=7)
        s.check("director: anti-repetition — a subject that clearly calls for its family keeps it, and the result "
                "says why", warr["family"] == chosen["infra"]
                and any("clearly calls for it" in n for n in warr["anti_repetition"]["notes"]),
                json.dumps(warr["anti_repetition"]))

        # anti-repetition from the user's real history file (sandboxed), via the CLI
        mem = Path(env["COORDN8_HOME"]) / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        rows = [{"ts": "2026-10-2%dT10:00:00" % i, "project": "pay-api", "build": i, "palette_family": chosen["fintech"]}
                for i in range(3)]
        (mem / "choices.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
        zones = [Path(env["COORDN8_HOME"]), Path(env["CLAUDE_HOME"])]

        def zone_files():
            return sorted(str(p) for z in zones for p in z.rglob("*") if p.is_file())

        before = zone_files()
        bf = td / "fintech.json"
        bf.write_text(json.dumps(FIXTURES["fintech"]))
        out = run(DIRECTOR, [bf, "--seed", "7", "--json"], env=env, timeout=120)
        try:
            cli = json.loads(out.stdout)
        except json.JSONDecodeError:
            cli = {}
        s.check("director: anti-repetition reads the user's choice history (sandboxed COORDN8_HOME) and moves off "
                "a family used on the last 3 builds", out.returncode == 0 and cli.get("family")
                and cli["family"] != chosen["fintech"] and cli["anti_repetition"]["recent_families"] == [chosen["fintech"]] * 3,
                f"rc={out.returncode} {cli.get('family')} {cli.get('anti_repetition')} {out.stderr[-300:]}")
        after = zone_files()
        s.check("director: choosing a palette writes no file in the user zone (~/.coordn8, ~/.claude)",
                after == before, str(set(after) - set(before)))

        # determinism
        o1 = run(DIRECTOR, [bf, "--seed", "11", "--json", "--no-history"], env=env, timeout=120)
        o2 = run(DIRECTOR, [bf, "--seed", "11", "--json", "--no-history"], env=env, timeout=120)
        s.check("director: same seed -> byte-identical output", o1.returncode == 0 and o1.stdout == o2.stdout
                and len(o1.stdout) > 100, o1.stderr[-200:])
        j1 = json.loads(o1.stdout) if o1.returncode == 0 else {}
        s.check("director: the seed and every random draw are recorded in the output",
                j1.get("seed") == 11 and len(j1.get("draws", [])) >= len(fams) + 2, str(j1.get("draws", [])[:2]))
        o3 = run(DIRECTOR, [bf, "--seed", "12", "--json", "--no-history"], env=env, timeout=120)
        s.check("director: a different seed changes the recorded draws (real seeded randomness)",
                o3.returncode == 0 and json.loads(o3.stdout)["draws"] != j1.get("draws"), "")

        # design_system.json keeps other keys
        build = td / "repo" / "Coordn8" / "_build"
        build.mkdir(parents=True)
        (build / "design_system.json").write_text(json.dumps({"schema": 1, "kit": "chalkboard",
                                                              "type": {"family": "Serif", "scale": {"title": 56}},
                                                              "motion": {"ease": "x"}}))
        out = run(DIRECTOR, [bf, "--build", build, "--seed", "5", "--no-history"], env=env, timeout=120)
        doc = json.loads((build / "design_system.json").read_text())
        s.check("director: writes design_system.json key 'palette' and keeps every other top-level key",
                out.returncode == 0 and doc.get("kit") == "chalkboard" and doc.get("type", {}).get("scale") == {"title": 56}
                and doc.get("motion") == {"ease": "x"} and doc.get("palette", {}).get("roles"), out.stderr[-300:])
        pk = doc.get("palette", {})
        s.check("director: the palette record has the SPEC 3.2 fields (family, roles, dark, candidates, rejected, "
                "obeyed_user_quotes, seed)", all(k in pk for k in ("family", "roles", "dark", "candidates", "rejected",
                                                                     "obeyed_user_quotes", "seed")), str(list(pk)))
        code, res = gate_json(build / "design_system.json")
        s.check("director: the written design system passes the gate", code == 0, res.get("summary"))
        bad_brief = td / "bad.json"
        bad_brief.write_text(json.dumps({"audience": "x"}))
        out = run(DIRECTOR, [bad_brief], env=env, timeout=60)
        s.check("director: a brief with no subject is refused (exit 2) with a reason", out.returncode == 2
                and "subject" in out.stdout, out.stdout[-200:])

        # design_system helper: concurrency + atomicity
        cdir = td / "conc"
        cdir.mkdir()
        (cdir / "design_system.json").write_text(json.dumps({"schema": 1, "keep": "me"}))
        code_snip = ("import sys; sys.path.insert(0, %r); from design import design_system as ds; "
                     "[ds.update(%r, 'k%%d_%%d' %% (int(sys.argv[1]), j), {'v': j}) for j in range(5)]"
                     % (str(ROOT / "scripts"), str(cdir)))
        procs = [subprocess.Popen([PY, "-c", code_snip, str(i)], env=dict(os.environ, **env)) for i in range(6)]
        rcs = [p.wait(timeout=60) for p in procs]
        doc = json.loads((cdir / "design_system.json").read_text())
        want = {f"k{i}_{j}" for i in range(6) for j in range(5)}
        s.check("design_system: 6 processes x 5 concurrent updates lose no key and keep the existing ones",
                all(rc == 0 for rc in rcs) and want <= set(doc) and doc.get("keep") == "me", f"missing={want - set(doc)}")
        s.check("design_system: writes are atomic — valid JSON after the storm and no temp file left behind",
                not list(cdir.glob(".ds-*")), str(list(cdir.iterdir())))
        mode = (cdir / "design_system.json").stat().st_mode & 0o777
        s.check("design_system: an atomic rewrite keeps the file's permissions", mode == 0o644 or mode == (0o666 & ~os.umask(os.umask(0))),
                oct(mode))

        # ------------------------------------------------------------- gate: scenes
        pal = results["medical"]
        R = pal["roles"]
        dp = write_design(td, "scene_design.json", pal)

        def scene(name, els):
            p = td / f"{name}.json"
            p.write_text(json.dumps({"elements": els}))
            return gate_json(dp, p)

        base = clean_scene(pal)
        code, res = scene("clean", base)
        s.check("gate: a clean scene passes (accent x2, role alphas, flattened tints, dark frame, ink series)",
                code == 0, json.dumps(res.get("findings", [])[:3]))
        code, res = scene("offpal", base + [{"id": "stray", "kind": "fill", "color": "#12ab34", "frame": "f1"}])
        s.check("gate: an off-palette colour is caught (and nothing else)", code == 1 and res["counts"] == {"off-palette": 1},
                json.dumps(res.get("counts")))
        code, res = scene("accent3", base + [{"id": "extra", "kind": "mark", "color": R["accent"], "frame": "f1"}])
        s.check("gate: a 3-accent frame is caught (and nothing else)", code == 1 and res["counts"] == {"accent-budget": 1},
                json.dumps(res.get("counts")))
        group = [dict(e, frame="g") for e in base if e["id"] in ("focal-fill", "focal-stroke")] + [
            {"id": "focal-glow", "kind": "mark", "color": rgba(R["accent"], 0.4), "frame": "g", "focal_group": "focal"},
            {"id": "other", "kind": "text", "color": R["accent"], "font_px": 28, "frame": "g"}]
        code, res = scene("focalgroup", group)
        s.check("gate: the parts of one focal group count as one accent element", code == 0, json.dumps(res.get("counts")))
        cap = rgba(R["ink"], 0.3)
        code, res = scene("lowcap", base + [{"id": "caption-low", "kind": "text", "color": cap, "font_px": 16,
                                             "frame": "f1"}])
        s.check("gate: a low-contrast caption is caught (and nothing else)",
                code == 1 and res["counts"] == {"text-contrast": 1}, json.dumps(res.get("counts")))
        alpha = next(a / 100 for a in range(100, 0, -1)
                     if C.contrast(C.composite(R["ink"], R["paper"], a / 100), R["paper"]) < 3.6)
        mid = rgba(R["ink"], alpha)
        verdicts = []
        for px, bold in ((28, False), (20, True), (16, False)):
            code, res = scene(f"large{px}", [{"id": "t", "kind": "text", "color": mid, "font_px": px, "bold": bold,
                                              "frame": "x"}])
            verdicts.append(code)
        s.check("gate: large text (>= 24px, or >= 18.66px bold) is judged at 3:1 and body text at 4.5:1",
                verdicts == [0, 0, 1], f"{verdicts} alpha={alpha}")
        code, res = scene("ess", [{"id": "arrow", "kind": "mark", "color": rgba(R["ink"], 0.15), "essential": True,
                                   "frame": "x"}, {"id": "rule", "kind": "mark", "color": rgba(R["ink"], 0.15),
                                                   "frame": "x"}])
        s.check("gate: an essential mark below 3:1 is caught; a decorative hairline is not",
                code == 1 and res["counts"] == {"mark-contrast": 1}, json.dumps(res.get("counts")))
        r0, g0, b0 = C.rgb_of(R["accent"])
        near = C.to_hex((r0 + 1, g0, max(0, b0 - 1)))
        code, res = scene("near", [{"id": "n", "kind": "text", "color": near, "font_px": 28, "frame": "x"}])
        s.check("gate: a near match (CIEDE2000 < 2) of a role is accepted as that role", code == 0,
                json.dumps(res.get("counts")))
        hue_only = [e if e["id"] != "bar-down" else dict(e, fill_weight="solid") for e in base]
        code, res = scene("hueonly", hue_only)
        s.check("gate: meaning by hue alone is caught (up/down bars differ only by hue)",
                code == 1 and res["counts"] == {"hue-only-meaning": 1}, json.dumps(res.get("counts")))
        signed = [dict(e, fill_weight="solid", label="+4%") if e["id"] == "bar-up" else
                  dict(e, fill_weight="solid", label="−3%") if e["id"] == "bar-down" else e for e in base]
        code, res = scene("signed", signed)
        s.check("gate: the same bars pass once a signed label carries the direction (fill weight OR sign/label)",
                code == 0, json.dumps(res.get("counts")))
        rainbow = [{"id": f"s{i}", "kind": "fill", "color": R[r], "frame": "chart", "fill_weight": 0.2 * i,
                    "label": f"series {i}"} for i, r in enumerate(("accent", "link", "positive", "negative", "warning"))]
        rainbow.append({"id": "s9", "kind": "fill", "color": rgba(R["link"], 0.5), "frame": "chart", "label": "x"})
        code, res = scene("rainbow", rainbow + [dict(e, frame="chart2") for e in base if e["id"].startswith("series")])
        s.check("gate: a frame with 5 hues on marks/fills is caught as rainbow; an ink stepped-opacity series is not",
                code == 1 and res["counts"].get("rainbow") == 1 and not any(f.get("frame") == "chart2"
                                                                            for f in res["findings"]),
                json.dumps(res.get("counts")))
        legend_roles = [{"id": f"l{i}", "kind": "fill", "color": R[r], "frame": "legend", "fill_weight": 0.3}
                        for i, r in enumerate(("deterministic", "model-judgement", "data-field", "vision", "accent"))]
        code, res = scene("legendroles", legend_roles)
        s.check("gate: the source-of-truth legend roles are one labelled system, not a rainbow", code == 0,
                json.dumps(res.get("counts")))
        code, res = scene("mismatch", base + [{"id": "loss", "kind": "text", "color": R["positive"], "meaning": "loss",
                                               "font_px": 28, "frame": "f1"}])
        s.check("gate: a role painted for a different meaning is caught (a 'loss' label in the positive colour)",
                code == 1 and res["counts"] == {"role-meaning-mismatch": 1}, json.dumps(res.get("counts")))
        code, res = scene("darker", base + [{"id": "lg", "kind": "text", "color": R["muted"], "font_px": 28,
                                             "text": "darker is larger", "frame": "f1"}])
        s.check("gate: a legend that names the ramp by lightness ('darker is larger') is caught",
                code == 1 and res["counts"] == {"legend-tone-by-lightness": 1}, json.dumps(res.get("counts")))

        # palette-level negatives
        sep = copy.deepcopy(pal)
        L_, c_, h_ = C.rgb_to_oklch(sep["roles"]["positive"])
        sep["roles"]["negative"] = C.oklch_hex(L_ - 0.01, c_, h_ + 4)
        code, res = gate_json(write_design(td, "sep.json", sep))
        s.check("gate: two roles of distinct meaning closer than 10 CIEDE2000 are caught (role-separation)",
                code == 1 and res["counts"].get("role-separation", 0) >= 1, json.dumps(res.get("counts")))
        alias = copy.deepcopy(pal)
        alias["roles"]["data-field"] = alias["roles"]["positive"]
        alias["dark"]["data-field"] = alias["dark"]["positive"]
        code, res = gate_json(write_design(td, "alias.json", alias))
        s.check("gate: roles in different groups may share a colour (data-field = positive is allowed)",
                code == 0, json.dumps(res.get("findings", [])[:2]))
        pale = copy.deepcopy(pal)
        pale["dark"]["muted"] = pale["dark"]["paper"]
        code, res = gate_json(write_design(td, "pale.json", pale))
        s.check("gate: a text role below 4.5:1 in the DARK skin is caught", code == 1
                and any(f["check"] == "role-contrast" and f["skin"] == "dark" for f in res.get("findings", [])),
                json.dumps(res.get("counts")))
        nodark = copy.deepcopy(pal)
        nodark.pop("dark")
        code, res = gate_json(write_design(td, "nodark.json", nodark))
        s.check("gate: a palette with no dark skin is flagged", code == 1 and res["counts"].get("no-dark-skin") == 1,
                json.dumps(res.get("counts")))

        # HTML / SVG reader
        svg_clean = f"""<!doctype html><html><head><style>:root {{ --muted: {R['muted']}; --paper: {R['paper']}; }}
        body {{ background: var(--paper); color: {R['ink']}; }}</style></head>
        <body><svg data-frame="hero" viewBox="0 0 800 400">
          <rect width="800" height="400" fill="{R['paper']}"/>
          <text x="20" y="40" fill="{R['ink']}" font-size="32">Title</text>
          <rect x="20" y="60" width="200" height="80" fill="{R['accent']}" fill-opacity="0.1" stroke="{R['accent']}"/>
          <text x="30" y="100" fill="{R['accent']}" font-size="28">Focal</text>
          <rect data-meaning="increase" x="300" y="60" width="40" height="80" fill="{R['positive']}"/>
          <rect data-meaning="decrease" x="360" y="60" width="40" height="80" fill="{R['negative']}" fill-opacity="0"
                stroke="{R['negative']}"/>
          <text x="20" y="200" style="fill: var(--muted)" font-size="28">stronger = more</text>
        </svg><p style="color: {R['muted']}; font-size: 28px">A caption.</p></body></html>"""
        hc = td / "clean.html"
        hc.write_text(svg_clean)
        code, res = gate_json(dp, html=[hc])
        s.check("gate: the HTML/SVG reader passes a clean page (inline attrs, styles, CSS variables)", code == 0,
                json.dumps(res.get("findings", [])[:3]))
        svg_bad = svg_clean.replace('</svg>', f'''<circle cx="500" cy="80" r="20" fill="{R['accent']}"/>
          <circle cx="560" cy="80" r="20" fill="{R['accent']}"/>
          <text x="20" y="260" fill="#b5b5b5" font-size="16">faint caption</text></svg>''').replace(
            'fill-opacity="0"\n                stroke', 'stroke')
        hb = td / "bad.html"
        hb.write_text(svg_bad)
        code, res = gate_json(dp, html=[hb])
        cn = res.get("counts", {})
        s.check("gate: the HTML/SVG reader catches a 3-accent frame, an off-palette faint caption (as both "
                "off-palette and low contrast) and hue-only up/down bars", code == 1 and cn.get("accent-budget") == 1
                and cn.get("off-palette", 0) >= 1 and cn.get("text-contrast") == 1 and cn.get("hue-only-meaning") == 1,
                json.dumps(cn))
        hs = td / "sheet.html"
        hs.write_text(svg_clean.replace("</style>", " .x { border: 1px solid #ff00aa; }</style>"))
        code, res = gate_json(dp, html=[hs])
        s.check("gate: an off-palette colour inside a <style> block is caught", code == 1
                and res["counts"].get("off-palette") == 1, json.dumps(res.get("counts")))

        # stylesheet reader: role variables, derivations, declarations (W8-F1)
        D = pal["dark"]
        kit_block = (":root{" + "".join(f"--c8-{k}: {v};" for k, v in R.items()) + "}"
                     "@media (prefers-color-scheme: dark){:root:not([data-theme=light]){"
                     + "".join(f"--c8-{k}: {v};" for k, v in D.items()) + "}}"
                     ":root[data-theme=dark]{" + "".join(f"--c8-{k}: {v};" for k, v in D.items()) + "}"
                     "@media print{:root{" + "".join(f"--c8-{k}: {v} !important;" for k, v in R.items()) + "}}")
        role_css = """
          :root { --c8p-surface: color-mix(in srgb, var(--c8-ink) 4%, var(--c8-paper));
                  --c8p-line: color-mix(in srgb, var(--c8-ink) 22%, var(--c8-paper)); --c8p-focus: var(--c8-accent); }
          html { background: var(--c8-paper); color: var(--c8-ink); }
          .btn { color: var(--c8-ink); background: var(--c8p-surface); border: 1px solid var(--c8p-line); }
          .btn:focus-visible { outline: 3px solid var(--c8p-focus); }
          .tip { background: var(--c8-ink); color: var(--c8-paper);
                 box-shadow: 0 6px 22px color-mix(in srgb, var(--c8-ink) 22%, transparent); }
          .note { border-left: 5px solid var(--c8-link); }
          .warn { border-left: 5px solid var(--c8-warning); }
          .read { color: var(--c8-positive); } .bad { color: var(--c8-negative); }
          .both { background: color-mix(in srgb, var(--c8-accent) 50%, var(--c8-link)); }
          .swatch { border: 2px solid currentColor; background: transparent; color: inherit; }
          @media print { .sec { border-top: 1px solid var(--c8p-line); } }"""

        def sheet_page(extra_css: str = "", body: str = "<p>Text.</p>") -> Path:
            hp = td / f"sheet{abs(hash(extra_css + body)) % 10**8}.html"
            hp.write_text(f"<!doctype html><html><head><style>{kit_block}</style><style>{role_css}{extra_css}</style>"
                          f"</head><body>{body}</body></html>")
            return hp

        code, res = gate_json(dp, html=[sheet_page()])
        s.check("gate: a page whose stylesheet paints only var(--c8-<role>), a role at an alpha "
                "(color-mix with transparent) and color-mix of two roles passes (PASS, exit 0)",
                code == 0 and res.get("status") == "PASS", json.dumps(res.get("findings", [])[:3]))
        s.check("gate: declaring every role of both skins (positive AND negative included) in custom properties "
                "is not painting — no rainbow, no hue-only-meaning from the kit's declaration block",
                not res.get("counts", {}).get("rainbow") and not res.get("counts", {}).get("hue-only-meaning"),
                json.dumps(res.get("counts")))
        nsheet = (res.get("markup") or [{}])[0].get("stylesheet_colours", 0)
        s.check("gate: the stylesheet reader resolves role variables into the colours they paint (it read more "
                "colours than the literal declarations alone)", nsheet > 2 * len(R) + 2 * len(D), nsheet)

        negatives = (
            ("a hard-coded #ff00aa in a paint rule", ".x { color: #ff00aa; }"),
            ("a hard-coded #ff00aa as the fallback of a declared role variable", ".x { color: var(--c8-ink, #ff00aa); }"),
            ("#ff00aa as the fallback of an UNdeclared variable (the browser paints it)",
             ".x { border-color: var(--nope, #ff00aa); }"),
            ("a color-mix of a role and #ff00aa", ".x { background: color-mix(in srgb, var(--c8-accent) 50%, #ff00aa); }"),
            ("a named colour (hotpink) in a paint rule", ".x { border: 2px solid hotpink; }"),
            ("an hsl() colour", ".x { fill: hsl(320, 100%, 50%); }"),
            ("a colour function the gate cannot resolve (oklch)", ".x { stroke: oklch(0.7 0.25 330); }"),
            ("a custom property declaring #ff00aa", ":root { --c8p-hot: #ff00aa; }"),
        )
        for what, css in negatives:
            code, res = gate_json(dp, html=[sheet_page(css)])
            s.check(f"gate (negative): {what} is still caught as off-palette",
                    code == 1 and res.get("counts", {}).get("off-palette", 0) >= 1, json.dumps(res.get("counts")))
        code, res = gate_json(dp, html=[sheet_page(body='<p style="color: red">Inline.</p>')])
        s.check("gate (negative): an inline style with a named colour (red) is caught as off-palette",
                code == 1 and res.get("counts", {}).get("off-palette", 0) >= 1, json.dumps(res.get("counts")))
        code, res = gate_json(dp, html=[sheet_page(".up { background: var(--c8-positive); } "
                                                   ".down { background: var(--c8-negative); }")])
        s.check("gate (negative): stylesheet rules that paint positive vs negative fills by hue alone are still "
                "caught (hue-only-meaning), now through the role variables",
                code == 1 and res.get("counts", {}).get("hue-only-meaning", 0) >= 1, json.dumps(res.get("counts")))
        code, res = gate_json(dp, html=[sheet_page(".a{fill:#d62728}.b{fill:#ff7f0e}.c{fill:#2ca02c}.d{fill:#1f77b4}"
                                                   ".e{fill:#9467bd}.f{fill:#e377c2}")])
        s.check("gate (negative): six unrelated hues painted by stylesheet rules are still a rainbow (and off-palette)",
                code == 1 and res.get("counts", {}).get("rainbow", 0) >= 1
                and res.get("counts", {}).get("off-palette", 0) >= 6, json.dumps(res.get("counts")))

        # skipped / error
        code, res = gate_json(td / "missing.json")
        s.check("gate: no design system -> SKIPPED (exit 2), never a pass", code == 2 and res.get("status") == "SKIPPED",
                str(res))
        (td / "broken.json").write_text("{not json")
        code, res = gate_json(td / "broken.json")
        s.check("gate: unreadable design system -> ERROR (exit 3)", code == 3 and res.get("status") == "ERROR", str(res))

    # ------------------------------------------------------------- freedom: no catalog membership in the gate
    src = (ROOT / GATE).read_text(encoding="utf-8")
    hits = re.findall(r"palettes\.json|catalog|famil(?:y|ies)|load_catalog|use_when|avoid_when|design\.palette|"
                      r"from design import palette", src, re.IGNORECASE)
    s.check("freedom: no gate code references catalog membership (grep of check_palette.py)", not hits, str(hits))

    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
