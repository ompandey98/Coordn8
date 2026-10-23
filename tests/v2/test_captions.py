#!/usr/bin/env python3
"""Captions, caption styles, transitions/continuity and the local cover (E1, E8, E9).

  E1  caption marks ⟦good:…⟧ ⟦bad:…⟧ ⟦key:…⟧ -> kit roles; <= 2 per caption; stripped from the
      spoken form and from the .srt; the .vtt keeps them as cue classes; subtitle fidelity
      compares unmarked text; v1.5.0 behaviour on text WITHOUT marks is byte-identical.
  E8  caption style library: every style >= 28 px at 1080p, <= 2 lines x 42 chars, inside the
      safe area, readable on every kit; an override below 28 px is clamped and truncated (with a
      finding), never shrunk; the overlay draws through the runtime's text primitive.
  E9  same-element morph, dissolve, cut-on-beat, push as pure functions of t; first-second
      anchor; the continuity gate; a cover rendered locally.

Hermetic: temp dirs, sandboxed HOME / COORDN8_HOME / CLAUDE_HOME, no network.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import site
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "render"))
sys.path.insert(0, str(ROOT / "scripts" / "gates"))
import caption_marks as cm  # noqa: E402
import pronounce  # noqa: E402
from design import kit_export  # noqa: E402

s = Suite("captions")
RENDER = ROOT / "scripts" / "render"
KITS = sorted((ROOT / "data" / "style_kits").glob("*.json"))
CHALK = ROOT / "data" / "style_kits" / "chalk-talk.json"
FIELD = ROOT / "data" / "style_kits" / "field-notebook.json"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def node_bin():
    for c in ("node", "/opt/homebrew/bin/node", "/usr/local/bin/node"):
        p = shutil.which(c) or (c if Path(c).exists() else None)
        if p:
            return p
    return None


NODE = node_bin()


def node(code: str, tmp: Path, payload=None):
    """Run a Node snippet with C (captions.js), T (transitions.js), R (scene_runtime.js) and IN."""
    f = tmp / "snippet.js"
    f.write_text(
        f"const C=require({json.dumps(str(RENDER / 'captions.js'))});\n"
        f"const T=require({json.dumps(str(RENDER / 'transitions.js'))});\n"
        f"const R=require({json.dumps(str(RENDER / 'scene_runtime.js'))});\n"
        f"const IN=JSON.parse(require('fs').readFileSync({json.dumps(str(tmp / 'in.json'))},'utf8'));\n"
        + code, encoding="utf-8")
    (tmp / "in.json").write_text(json.dumps(payload or {}), encoding="utf-8")
    cp = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    try:
        return json.loads(cp.stdout.strip().splitlines()[-1]), cp
    except Exception:  # noqa: BLE001
        return None, cp


def gate(script, args, env):
    r = run(script, args, env=env, timeout=120)
    try:
        out = json.loads(r.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        out = {}
    return r, out


def srt_texts(body: str) -> list:
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ti is not None:
            out.append(lines[ti + 1:])
    return out


chalk = load(CHALK)
chalk_dark = chalk["palette"]["dark"]          # chalk-talk renders dark (base)
MARKED = "The cache turns a ⟦bad:slow read⟧ into a ⟦good:fast one⟧ — the ⟦key:hit rate⟧ matters."

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)

    # ======================================================================== E1 — marks -> roles
    sp = cm.spans(MARKED, chalk)
    roles = {x["name"]: x["role"] for x in sp["spans"]}
    cols = {x["name"]: x["color"] for x in sp["spans"]}
    s.check("marked display text resolves good/bad/key to positive/negative/accent roles of the active kit",
            roles == {"bad": "negative", "good": "positive", "key": "accent"}, roles)
    s.check("each mark is coloured with the active kit's own role colour (base skin)",
            cols == {"bad": chalk_dark["negative"], "good": chalk_dark["positive"], "key": chalk_dark["accent"]},
            cols)
    s.check("each mark carries a non-colour cue from the kit (meaning never by hue alone)",
            all(x["styles"] for x in sp["spans"]), sp["spans"])
    skin = {"family": "invented-teal", "roles": dict(chalk["palette"]["roles"], accent="#005f73"),
            "dark": dict(chalk_dark, accent="#94d2bd")}
    sk = cm.spans("the ⟦key:hit rate⟧", chalk, skin)
    s.check("a Palette Director skin replaces the kit colours for marks (one design system per build)",
            sk["spans"][0]["color"] == "#94d2bd", sk["spans"])
    inv = json.loads(json.dumps(chalk))
    inv["caption_style"]["marks"]["win"] = {"role": "positive", "styles": ["weight-800"]}
    iv = cm.spans("a ⟦win:clean sweep⟧", inv)
    s.check("an invented mark the kit declares resolves to its declared role (menus, not fences)",
            iv["spans"][0]["role"] == "positive" and not iv["problems"], iv)
    s.check("a mark named after a palette role resolves to that role directly",
            cm.spans("a ⟦warning:stale entry⟧", chalk)["spans"][0]["role"] == "warning", "")
    s.check("strip() removes every mark and is the identity on unmarked text",
            cm.strip(MARKED) == "The cache turns a slow read into a fast one — the hit rate matters."
            and cm.strip("Plain [text] with (brackets).") == "Plain [text] with (brackets).", cm.strip(MARKED))
    s.check("the alias form ⟦key⟧…⟦/key⟧ is understood and reported as a note",
            cm.parse("a ⟦key⟧hot⟦/key⟧ b")["plain"] == "a hot b" and cm.parse("a ⟦key⟧hot⟦/key⟧ b")["notes"], "")
    mal = cm.parse("a ⟦key:open and ⟦good:x⟧⟧ c")
    s.check("malformed (nested / unclosed) markup is reported and never survives into plain text",
            mal["errors"] and "⟦" not in mal["plain"] and "⟧" not in mal["plain"], mal)

    # ---- the gate
    def caps_gate(sentences, kit=None, name="cap.json"):
        p = tmp / name
        p.write_text(json.dumps({"sentences": sentences}), encoding="utf-8")
        args = [str(p)] + (["--kit", str(kit)] if kit else [])
        return gate("scripts/gates/check_caption_marks.py", args, env)

    r, out = caps_gate([{"idx": 1, "text": "A ⟦good:fast⟧ and ⟦bad:slow⟧ and ⟦key:hot⟧ path.", "start": 0, "end": 3}],
                       CHALK)
    s.check("three marks in one caption are flagged by check_caption_marks (exit 1)",
            r.returncode == 1 and any("3 marks" in f for f in out.get("findings", [])), r.stdout[-400:])
    r, out = caps_gate([{"idx": 1, "text": MARKED.replace("⟦key:hit rate⟧", "hit rate"), "start": 0, "end": 3}],
                       CHALK)
    s.check("two marks in one caption pass check_caption_marks (exit 0)", r.returncode == 0, r.stdout[-400:])
    long3 = ("The ⟦good:cache⟧ answers the first read quickly and the second one too, while the "
             "⟦bad:database⟧ waits on a disk that is shared by every tenant, so the ⟦key:hit rate⟧ decides it.")
    r, out = caps_gate([{"idx": 1, "text": long3, "start": 0, "end": 9}], CHALK, "long.json")
    s.check("three marks spread over separate captions of one sentence pass (the cap is per caption)",
            r.returncode == 0, r.stdout[-400:])
    r, out = caps_gate([{"idx": 1, "text": "The ⟦shiny:thing⟧ moved.", "start": 0, "end": 3}], CHALK, "u.json")
    s.check("an unknown mark name is flagged (it cannot be coloured)",
            r.returncode == 1 and any("unknown mark" in f for f in out.get("findings", [])), r.stdout[-400:])
    nr = json.loads(json.dumps(chalk))
    nr["caption_style"]["marks"]["glow"] = {"role": "sparkle", "styles": ["underline"]}
    (tmp / "norole.json").write_text(json.dumps(nr), encoding="utf-8")
    r, out = caps_gate([{"idx": 1, "text": "It ⟦glow:lights up⟧.", "start": 0, "end": 3}], tmp / "norole.json", "nr.json")
    s.check("a mark mapping to a role the design system does not declare is flagged",
            r.returncode == 1 and any("does not declare" in f for f in out.get("findings", [])), r.stdout[-400:])
    r, out = caps_gate([{"idx": 1, "text": "Follow the ⟦key:SOP⟧.", "speech": "Follow the ⟦key:S O P⟧.",
                         "start": 0, "end": 3}], CHALK, "sp.json")
    s.check("mark syntax in the narration spoken form is flagged",
            r.returncode == 1 and any("spoken form" in f for f in out.get("findings", [])), r.stdout[-400:])
    r, out = caps_gate([{"idx": 1, "text": "The ⟦good:cache⟧ helps.", "start": 0, "end": 3},
                        {"idx": 2, "text": "A cold ⟦bad:cache⟧ hurts.", "start": 3, "end": 6}], CHALK, "mm.json")
    s.check("the same term marked good in one caption and bad in another is flagged (one colour, one meaning)",
            r.returncode == 1 and any("different meanings" in f for f in out.get("findings", [])), r.stdout[-400:])
    r, out = gate("scripts/gates/check_caption_marks.py", [str(tmp / "nope.json")], env)
    s.check("check_caption_marks with no input is SKIPPED (exit 2)", r.returncode == 2, r.stdout[-200:])

    # ---- spoken form
    spoken = pronounce.to_speech("Follow the ⟦key:SOP⟧; ⟦good:NASA⟧ ships JSON over the ⟦bad:API⟧.")
    s.check("spoken form contains no mark syntax", not cm.has_marks(spoken), spoken)
    s.check("pronounce still spells SOP inside a mark ('S O P') and leaves NASA/JSON as words",
            "S O P" in spoken and "A P I" in spoken and "NASA" in spoken and "J S O N" not in spoken, spoken)
    s.check("SSML spoken form of a marked initialism is the standard say-as, with no marks",
            pronounce.to_speech("the ⟦key:SOP⟧", ssml=True) == 'the <say-as interpret-as="characters">SOP</say-as>',
            pronounce.to_speech("the ⟦key:SOP⟧", ssml=True))
    per_word = [pronounce.to_speech(w) for w in "the ⟦key:cache miss⟧ path".split()]
    s.check("a mark split across words never reaches the voice (per-word spoken form is clean)",
            not any(cm.has_marks(w) for w in per_word) and per_word == ["the", "cache", "miss", "path"], per_word)
    bm = tmp / "bm.json"
    sentences = [{"idx": 1, "text": "Follow the ⟦key:SOP⟧ before you deploy.", "start": 0.0, "end": 3.0},
                 {"idx": 2, "text": "The cache turns a ⟦bad:slow read⟧ into a ⟦good:fast one⟧.", "start": 3.2,
                  "end": 6.4},
                 {"idx": 3, "text": "Then it writes the result back to the shared store for every reader that "
                                    "comes after it.", "start": 6.4, "end": 12.0}]
    bm.write_text(json.dumps({"fps": 30, "sentences": sentences}), encoding="utf-8")
    sp_out = tmp / "bm.speech.json"
    pronounce.transform_beatmap(bm, sp_out, ssml=False, spell=None, word=None)
    tb = load(sp_out)["sentences"]
    s.check("beatmap transform: display text keeps its marks, the speech field is mark-free and spelled",
            tb[0]["text"] == sentences[0]["text"] and tb[0]["speech"] == "Follow the S O P before you deploy."
            and not cm.has_marks(tb[1]["speech"]), tb[:2])

    # ---- subtitles
    srt, vtt, vtt_plain = tmp / "o.srt", tmp / "o.vtt", tmp / "plain.vtt"
    r = run("scripts/render/subtitles.py", ["srt", str(bm), "--out", str(srt)], env=env)
    body = srt.read_text(encoding="utf-8") if srt.exists() else ""
    s.check(".srt text built from marked narration contains no mark syntax and reads SOP",
            r.returncode == 0 and not cm.has_marks(body) and "SOP" in body and "S O P" not in body, body[:300])
    r, out = gate("scripts/gates/subtitle_check.py", [str(srt), str(bm)], env)
    s.check("subtitle_check passes the .srt of marked narration (it compares unmarked text)",
            r.returncode == 0, r.stdout[-400:])
    r = run("scripts/render/subtitles.py", ["vtt", str(bm), "--out", str(vtt), "--kit", str(CHALK)], env=env)
    vbody = vtt.read_text(encoding="utf-8") if vtt.exists() else ""
    s.check(".vtt keeps marks as WebVTT cue classes (<c.key>SOP</c>) and never as raw syntax",
            "<c.key>SOP</c>" in vbody and "<c.bad>slow read</c>" in vbody and not cm.has_marks(vbody), vbody[:400])
    s.check(".vtt --kit embeds a STYLE block colouring each mark class with the kit colour",
            "STYLE" in vbody and f"::cue(.key) {{ color: {chalk_dark['accent']}" in vbody, vbody[:300])
    r, out = gate("scripts/gates/subtitle_check.py", [str(vtt), str(bm)], env)
    s.check("subtitle_check passes the .vtt with cue classes", r.returncode == 0, r.stdout[-400:])
    run("scripts/render/subtitles.py", ["vtt", str(bm), "--out", str(vtt_plain), "--no-classes"], env=env)
    pb = vtt_plain.read_text(encoding="utf-8") if vtt_plain.exists() else ""
    s.check("vtt --no-classes writes plain caption text", "<c." not in pb and "Follow the SOP" in pb, pb[:200])
    leak = tmp / "leak.srt"
    leak.write_text(body.replace("slow read", "⟦bad:slow read⟧"), encoding="utf-8")
    r, out = gate("scripts/gates/subtitle_check.py", [str(leak), str(bm)], env)
    s.check("subtitle_check flags raw mark syntax that leaked into a caption (exit 1)",
            r.returncode == 1 and any("raw caption-mark" in f for f in out.get("findings", [])), r.stdout[-400:])
    tam = tmp / "tam.srt"
    tam.write_text(body.replace("slow", "SLUGGISH"), encoding="utf-8")
    r, out = gate("scripts/gates/subtitle_check.py", [str(tam), str(bm)], env)
    s.check("subtitle_check still flags a changed word against marked narration (exit 1)",
            r.returncode == 1 and any("differ from narration" in f for f in out.get("findings", [])), r.stdout[-300:])

    # ---- v1.5.0 identity on text WITHOUT marks (the existing gate's verdicts must not move)
    zips = [ROOT / "coordn8-v1.5.0.zip",
            Path(os.environ.get("C8_BASELINE_ZIP", str(ROOT.parent / "Coordn8_v2_handoff" / "_baseline" / "coordn8-v1.5.0.zip")))]
    zp = next((z for z in zips if z.exists()), None)
    if zp is None:
        s.check("v1.5.0 baseline not present here — identity comparison SKIPPED (recorded)", True, "no zip")
    else:
        old = tmp / "v150"
        with zipfile.ZipFile(zp) as z:
            for n in z.namelist():
                if re.search(r"scripts/(render/(subtitles|pronounce)\.py|gates/subtitle_check\.py|lib/common\.py)$", n):
                    dest = old / n[n.index("scripts/"):]
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(n))
        plain_bms = {
            "short": [{"idx": 1, "text": "The router  parses the request.", "start": 0.0, "end": 3.0},
                      {"idx": 2, "text": "", "start": 3.0, "end": 3.1},
                      {"idx": 3, "text": "Then it dispatches to a handler [v2] (fast).", "start": 3.1, "end": 6.4}],
            "slow": [{"idx": 1, "text": ("The request enters the router and is immediately classified by its path "
                                         "prefix, then handed to the matching handler which validates the payload."),
                      "start": 0.0, "end": 18.0},
                     {"idx": 2, "text": "Done.", "start": 18.0, "end": 30.0}],
        }
        same_srt = same_vtt = same_gate = True
        detail = []
        for name, sents in plain_bms.items():
            b = tmp / f"plain_{name}.json"
            b.write_text(json.dumps({"fps": 30, "sentences": sents}), encoding="utf-8")
            for kind in ("srt", "vtt"):
                a_out, o_out = tmp / f"new_{name}.{kind}", tmp / f"old_{name}.{kind}"
                run("scripts/render/subtitles.py", [kind, str(b), "--out", str(a_out)], env=env)
                subprocess.run([sys.executable, str(old / "scripts/render/subtitles.py"), kind, str(b), "--out",
                                str(o_out)], capture_output=True, text=True, env={**os.environ, **env})
                eq = a_out.exists() and o_out.exists() and a_out.read_bytes() == o_out.read_bytes()
                if kind == "srt":
                    same_srt &= eq
                else:
                    same_vtt &= eq
                if not eq:
                    detail.append(f"{name}.{kind}")
                # gate verdicts on the faithful file, a tampered copy and a dropped cue
                txt = o_out.read_text(encoding="utf-8") if o_out.exists() else ""
                variants = {"ok": txt, "tamper": txt.replace("router", "ROUTER-X"),
                            "drop": "1\n00:00:00,000 --> 00:00:03,000\nThe router parses the request.\n"}
                for vn, vt in variants.items():
                    vp = tmp / f"var_{name}_{vn}.{kind}"
                    vp.write_text(vt, encoding="utf-8")
                    a = run("scripts/gates/subtitle_check.py", [str(vp), str(b)], env=env)
                    o = subprocess.run([sys.executable, str(old / "scripts/gates/subtitle_check.py"), str(vp), str(b)],
                                       capture_output=True, text=True, env={**os.environ, **env})
                    fa = json.loads(a.stdout.splitlines()[0]).get("findings") if a.stdout.strip() else None
                    fo = json.loads(o.stdout.splitlines()[0]).get("findings") if o.stdout.strip() else None
                    if a.returncode != o.returncode or fa != fo:
                        same_gate = False
                        detail.append(f"gate {name}.{kind}.{vn}: {a.returncode}/{o.returncode}")
        s.check("unmarked narration: .srt output is byte-identical to v1.5.0", same_srt, detail)
        s.check("unmarked narration: .vtt output is byte-identical to v1.5.0", same_vtt, detail)
        s.check("unmarked narration: subtitle_check exit codes and findings are identical to v1.5.0 "
                "(faithful, tampered, dropped)", same_gate, detail)
        sys.path.insert(0, str(old / "scripts" / "render"))
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("pronounce_v150", old / "scripts/render/pronounce.py")
        pv = _ilu.module_from_spec(spec)
        spec.loader.exec_module(pv)
        samples = ["Follow the SOP; NASA ships JSON over the API.", "We keep three SOPs [and] (POD) notes.",
                   "The SQL GUI reads a POD.", "OK, REST is fine; RBAC/HTTPS too.", ""]
        same_sp = all(pv.to_speech(x) == pronounce.to_speech(x) and
                      pv.to_speech(x, ssml=True) == pronounce.to_speech(x, ssml=True) for x in samples)
        s.check("unmarked text: pronounce spoken form (plain and SSML) is identical to v1.5.0", same_sp, samples)

    # ======================================================================== E8 — caption styles
    styles = cm.load_styles()["styles"]
    ids = {x["id"] for x in styles}
    s.check("at least 6 caption styles ship in data/caption_styles.json", len(styles) >= 6, len(styles))
    s.check("the style library covers lower-third scrim, boxed karaoke, minimal outline, centre title, side-note "
            "and kids bold", {"lower-third-scrim", "boxed-karaoke", "minimal-outline", "centre-title", "side-note",
                              "kids-bold"} <= ids, ids)
    s.check("every caption style is at least 28 px at 1080p", all(x["font_px"] >= 28 for x in styles),
            [(x["id"], x["font_px"]) for x in styles])
    s.check("every caption style keeps max_lines <= 2 and max_chars_per_line <= 42",
            all(x["max_lines"] <= 2 and x["max_chars_per_line"] <= 42 for x in styles), "")
    errs = [e for x in styles for e in cm.validate_style(x)]
    s.check("every caption style's box sits inside the 5% safe area and passes the outcome rules", not errs, errs)
    bad_c = []
    for k in KITS:
        kd = load(k)
        for mode in ("roles", "dark"):
            for x in styles:
                bad_c += [f"{k.stem}/{mode}: {e}" for e in cm.style_contrast(x, kd["palette"][mode])]
    s.check("every caption style keeps caption text >= 4.5:1 over what is behind it, on every kit, both skins",
            not bad_c, bad_c[:5])
    s.check("every caption style declares its current-word highlight (none / word / phrase)",
            all((x.get("highlight") or {}).get("mode") in ("none", "word", "phrase") for x in styles), "")
    s.check("a style below 28 px is rejected by validate_style (never shrink)",
            any("28 px floor" in e for e in cm.validate_style(dict(styles[0], id="tiny", font_px=22))), "")
    s.check("a style whose box leaves the safe area is rejected",
            any("safe area" in e for e in cm.validate_style(dict(styles[0], id="edge",
                                                                  position={"anchor": "bottom", "align": "left",
                                                                            "inset_pct": 1, "width_pct": 60}))), "")
    invented = {"id": "invented-ticker", "font_px": 30, "max_lines": 1, "max_chars_per_line": 36,
                "line_height": 1.2, "overflow": "truncate",
                "position": {"anchor": "top", "align": "left", "inset_pct": 6, "width_pct": 50},
                "text": {"role": "ink"}, "scrim": {"role": "paper", "alpha": 0.9, "pad_px": 14},
                "highlight": {"mode": "word", "style": "underline", "role": "accent"}}
    s.check("an invented caption style passes when its outcomes are good (menus, not fences)",
            not cm.validate_style(invented) and not cm.style_contrast(invented, chalk_dark), cm.validate_style(invented))
    rs = cm.resolve_style([x for x in styles if x["id"] == "side-note"][0], chalk, font_px=22)
    s.check("resolve_style clamps a 22 px override to 28 px and records the clamp as a finding",
            rs["font_px"] == 28 and rs["findings"] and rs["findings"][0]["code"] == "font_clamped", rs["findings"])
    s.check("resolve_style resolves the style's roles and the kit's marks to colours",
            rs["colors"]["text"] == chalk_dark["ink"] and rs["marks"]["key"]["color"] == chalk_dark["accent"],
            rs["colors"])

    if not NODE:
        s.check("node not available — captions.js / transitions.js checks SKIPPED (recorded)", True, "no node")
    else:
        side = [x for x in styles if x["id"] == "side-note"][0]
        text84 = ("The cache keeps each verdict the classifier made so a repeat request skips the model call")
        res, cp = node("const L=C.layout(IN.text, IN.style, {font_px: 22});"
                       "console.log(JSON.stringify(L));", tmp, {"text": text84, "style": side})
        ok = res is not None
        s.check("captions.js: a 22 px override is clamped to 28 px (never shrunk)",
                ok and res["font_px"] == 28 and any(f["code"] == "font_clamped" for f in res["findings"]),
                (res or {}).get("findings") or cp.stderr[-300:])
        lines = [" ".join(w["text"] for w in ln["words"]) for ln in (res or {}).get("lines", [])]
        s.check("captions.js: the clamped caption is truncated with an ellipsis and a truncation finding",
                ok and res["truncated"] and len(lines) <= 2 and lines[-1].endswith("…")
                and any(f["code"] == "truncated" for f in res["findings"]), lines)
        res, cp = node("const L=C.layout(IN.text, IN.style, {});console.log(JSON.stringify(L));", tmp,
                       {"text": "A short caption.", "style": side})
        s.check("captions.js: a caption that fits is neither clamped nor truncated",
                res is not None and res["font_px"] == 30 and not res["truncated"] and not res["findings"],
                (res or {}).get("findings"))
        res, cp = node("const out=[];for(const st of IN.styles){const L=C.layout(IN.text, st, {});"
                       "out.push([st.id, L.px, L.box.x, L.box.y, L.box.w, L.box.h]);}console.log(JSON.stringify(out));",
                       tmp, {"styles": styles, "text": "The cache keeps each verdict so a repeat skips the call"})
        inside = res is not None and all(px >= 28 and x >= 96 - 0.5 and y >= 54 - 0.5 and x + w <= 1824 + 0.5
                                         and y + h <= 1026 + 0.5 for _i, px, x, y, w, h in res)
        s.check("captions.js: every library style lays out at >= 28 px inside the safe area", inside, res)

        kit_rt = kit_export.to_runtime(chalk)
        style_rt = cm.resolve_style([x for x in styles if x["id"] == "boxed-karaoke"][0], chalk)
        cues = [{"text": "A ⟦good:fast⟧ path, a ⟦bad:slow⟧ path and a ⟦key:hot⟧ path.", "start": 0.0, "end": 3.0}]
        res, cp = node("const r=C.draw(null, 1.0, {cues: IN.cues, kit: IN.kit, style: IN.style});"
                       "console.log(JSON.stringify(r));", tmp, {"cues": cues, "kit": kit_rt, "style": style_rt})
        drawn = (res or {}).get("drawn", [])
        by = {d["text"].strip(".,"): d for d in drawn}
        s.check("captions.js colours marked words with the active kit's role colour",
                by.get("fast", {}).get("color") == chalk_dark["positive"]
                and by.get("slow", {}).get("color") == chalk_dark["negative"], [(d["text"], d["color"]) for d in drawn])
        s.check("captions.js draws at most 2 marks per caption; the third is drawn plain with a finding",
                by.get("hot", {}).get("mark") is None
                and any(f["code"] == "too_many_marks" for f in (res or {}).get("findings", [])),
                (res or {}).get("findings"))
        # word timings in the alignment stream's exact shape (scripts/render/align.py)
        wfix = ROOT / "tests" / "v2" / "fixtures" / "align" / "words_sample.json"
        words_doc = {"lines": [{"name": "l1", "text": "A fast path, a slow path and a hot path.", "start": 0.2,
                                "end": 3.0, "words": [{"w": w.strip(".,"), "start": 0.2 + 0.25 * i,
                                                       "end": 0.2 + 0.25 * (i + 1), "i": i}
                                                      for i, w in enumerate("A fast path, a slow path and a hot path."
                                                                            .split())]}],
                     "source": "engine", "fps": 30}
        res, cp = node("const r=C.draw(null, 0.2+0.25*4+0.1, {cues: IN.cues, kit: IN.kit, style: IN.style, words: IN.words});"
                       "console.log(JSON.stringify({timing:r.timing, cur:r.drawn.filter(d=>d.current).map(d=>d.text)}));",
                       tmp, {"cues": cues, "kit": kit_rt, "style": style_rt, "words": words_doc})
        s.check("captions.js highlights the current word from window.__C8_WORDS__ word timings",
                res is not None and res["timing"] == "word-timings" and res["cur"] == ["slow"], res)
        if not wfix.exists():
            s.check("alignment fixture words_sample.json absent — fixture highlight check SKIPPED (recorded)", True,
                    str(wfix))
        else:
            wdoc = load(wfix)
            fsent = [{"name": ln["name"], "text": ln["text"], "start": ln["start"], "end": ln["end"]}
                     for ln in wdoc["lines"]]
            probes = []           # (t, line name, expected word index) — every probe read from the fixture
            for ln in wdoc["lines"]:
                for w in ln["words"]:
                    probes.append([round((w["start"] + w["end"]) / 2, 3), ln["name"], w["i"], w["w"]])
            res, cp = node(
                "const cues=C.segment(IN.s); const st=Object.assign({},C.DEFAULT_STYLE,{highlight:{mode:'word',style:'box'}});"
                "const out=IN.p.map(([t,name,i,w])=>{const r=C.draw(null,t,{cues,style:st,words:IN.w});"
                "const cur=r.drawn.filter(d=>d.current); return {t,name,i,w,timing:r.timing,line:r.timing_line,"
                "got:cur.map(d=>d.text), wi:cur.map(d=>r.layout.lines.flatMap(l=>l.words)[r.drawn.indexOf(d)].wi)};});"
                "const ext=C.draw(null, IN.ext.t, {cues:[IN.ext.cue], style:st, words:IN.w});"
                "const bad=C.draw(null, 1.3, {cues:[{text:'Follow the SOP after lunch.',start:0.5,end:2.6}], style:st, words:IN.w});"
                "console.log(JSON.stringify({out, ext:{timing:ext.timing, line:ext.timing_line, got:ext.drawn.filter(d=>d.current).map(d=>d.text)},"
                "bad:{timing:bad.timing, reason:bad.timing_reason}}));",
                tmp, {"s": fsent, "w": wdoc, "p": probes,
                      "ext": {"t": 5.6, "cue": {"text": "then the checks report.", "start": 5.1, "end": 6.4}}})
            rows = (res or {}).get("out", [])
            okf = rows and all(r["timing"] == "word-timings" and r["line"] == r["name"] and r["wi"] == [r["i"]]
                               and [x.strip(".,") for x in r["got"]] == [r["w"]] for r in rows)
            s.check("captions.js highlights the right word at t from the alignment fixture words_sample.json "
                    "(line by name, word by index — a repeated word resolves to the spoken occurrence)",
                    okf, [r for r in rows if not (r["wi"] == [r["i"]])][:3] or cp.stderr[-300:])
            ex = (res or {}).get("ext", {})
            s.check("captions.js finds the alignment line by time for a cue with no line name",
                    ex.get("timing") == "word-timings" and ex.get("line") == "checks" and ex.get("got") == ["checks"], ex)
            bd = (res or {}).get("bad", {})
            s.check("captions.js falls back to the estimate, with the reason, when the timings do not match the "
                    "caption's words", bd.get("timing") == "estimate" and "do not match" in (bd.get("reason") or ""), bd)
        res, cp = node("const r=C.draw(null, 2.9, {cues: IN.cues, kit: IN.kit, style: IN.style});"
                       "console.log(JSON.stringify({timing:r.timing, cur:r.drawn.filter(d=>d.current).map(d=>d.text)}));",
                       tmp, {"cues": cues, "kit": kit_rt, "style": style_rt})
        s.check("captions.js falls back to a character-proportional estimate without word timings",
                res is not None and res["timing"] == "estimate" and res["cur"] == ["path."], res)
        res, cp = node(
            "const sc=new R.Scene({name:'cap', script:[{name:'l1', text:'x', start:0, end:3}], ctx:null});"
            "sc.begin(1.0); C.draw(sc, 1.0, {cues: IN.cues, kit: IN.kit, style: IN.style}); const f=sc.end();"
            "console.log(JSON.stringify(f.boxes.map(b=>({kind:b.kind, text:b.text||null, size:b.size||null}))));",
            tmp, {"cues": cues, "kit": kit_rt, "style": style_rt})
        texts = [b for b in (res or []) if b["kind"] == "text"]
        s.check("captions.js draws through the runtime's text primitive: caption words appear in the scene trace "
                "at >= 28 px with their scrim", bool(texts) and all(b["size"] >= 28 for b in texts)
                and any(b["kind"] == "caption-scrim" for b in res or []) and " ".join(b["text"] for b in texts)
                .startswith("A fast path,"), (res or cp.stderr[-300:]))
        # segmentation parity with subtitles.py (word stream AND cue boundaries)
        segbm = [{"text": x["text"], "start": x["start"], "end": x["end"]} for x in sentences] + [
            {"text": "That result is written back to the cache.", "start": 12.0, "end": 24.0}]
        b2 = tmp / "seg.json"
        b2.write_text(json.dumps({"sentences": segbm}), encoding="utf-8")
        run("scripts/render/subtitles.py", ["srt", str(b2), "--out", str(tmp / "seg.srt")], env=env)
        py_cues = [" ".join(" ".join(c).split()) for c in srt_texts((tmp / "seg.srt").read_text(encoding="utf-8"))]
        res, cp = node("console.log(JSON.stringify(C.segment(IN.s).map(c=>[c.text,c.start,c.end])));", tmp,
                       {"s": segbm})
        js_cues = [x[0] for x in (res or [])]
        s.check("captions.js cue segmentation matches the .srt cue for cue (same words, same boundaries)",
                js_cues == py_cues, {"js": js_cues, "py": py_cues})
        src = (RENDER / "captions.js").read_text(encoding="utf-8") + (RENDER / "transitions.js").read_text(
            encoding="utf-8")
        s.check("captions.js and transitions.js use no clock, randomness or CSS animation (pure functions of t)",
                not re.search(r"Math\.random|Date\.now|new Date|performance\.now|requestAnimationFrame|"
                              r"@keyframes|animation\s*:", src), "")

        # ==================================================================== E9 — transitions
        fn_rt = kit_export.to_runtime(load(FIELD))
        A = [{"id": "cache", "x": 100, "y": 200, "w": 300, "h": 120, "color": "#ff0000", "alpha": 1},
             {"id": "old", "x": 0, "y": 0, "w": 50, "h": 50}]
        B = [{"id": "cache", "x": 900, "y": 600, "w": 500, "h": 200, "color": "#0000ff", "alpha": 1},
             {"id": "new", "x": 10, "y": 10, "w": 50, "h": 50}]
        res, cp = node(
            "const at=(t)=>T.compose(t,{from:IN.A,to:IN.B,kind:'morph',at:1.0,kit:IN.kit});"
            "const g=(st,id)=>st.elements.find(e=>e.id===id);"
            "const mid=at(1.0+0.26), s0=at(1.0), s1=at(1.0+0.52), again=at(1.0+0.26);"
            "console.log(JSON.stringify({mid:g(mid,'cache'), p:mid.p, s0:g(s0,'cache'), s1:g(s1,'cache'),"
            "same: JSON.stringify(mid)===JSON.stringify(again), old:g(mid,'old'), nw:g(mid,'new'),"
            "old1:g(s1,'old'), new1:g(s1,'new'), dur:mid.dur, kind:mid.kind, in_kit:mid.in_kit}));",
            tmp, {"A": A, "B": B, "kit": fn_rt})
        m = res or {}
        mid = m.get("mid") or {}
        s.check("same-element morph: mid-transition position lies strictly between the two scenes",
                100 < mid.get("x", 0) < 900 and 200 < mid.get("y", 0) < 600 and 0 < m.get("p", 0) < 1, m)
        s.check("same-element morph: size and colour interpolate too (not a cut)",
                300 < mid.get("w", 0) < 500 and 120 < mid.get("h", 0) < 200 and mid.get("color") not in
                ("#ff0000", "#0000ff"), mid)
        s0, s1 = m.get("s0") or {}, m.get("s1") or {}
        s.check("same-element morph: p=0 reproduces scene A and p=1 reproduces scene B exactly (no jump at either end)",
                (s0.get("x"), s0.get("y"), s0.get("w"), s0.get("color")) == (100, 200, 300, "#ff0000")
                and (s1.get("x"), s1.get("y"), s1.get("w"), s1.get("color")) == (900, 600, 500, "#0000ff"), (s0, s1))
        s.check("same-element morph is identical for the same t (deterministic)", m.get("same") is True, "")
        s.check("morph: unmatched outgoing elements fade out and unmatched incoming ones fade in",
                (m.get("old1") or {}).get("alpha", 1) == 0 and (m.get("new1") or {}).get("alpha") == 1
                and (m.get("old") or {}).get("morph") == "out" and (m.get("nw") or {}).get("morph") == "in", m)
        s.check("morph duration and easing come from the active kit's vocabulary",
                m.get("kind") == "morph" and m.get("in_kit") is True and abs(m.get("dur", 0) - 0.52) < 1e-9, m)

        allk = {k.stem: kit_export.to_runtime(load(k)) for k in KITS}
        res, cp = node("const out={};for(const k of Object.keys(IN)){out[k]=T.vocabulary(IN[k]).map(e=>e.kind);}"
                       "console.log(JSON.stringify(out));", tmp, allk)
        kinds_ok = res is not None and all("morph" in v and all(x in ("morph", "dissolve", "cut", "push", "wipe",
                                                                         "dip", "zoom") for x in v)
                                           for v in res.values())
        s.check("every kit's transition vocabulary classifies into known kinds and offers a morph", kinds_ok, res)
        ops_rt = allk.get("ops-console")
        res, cp = node(
            "const A=IN.A,B=IN.B;"
            "const d1=T.compose(1.25,{from:A,to:B,kind:'dissolve',at:1,dur:0.5}), d2=T.compose(1.25,{from:A,to:B,kind:'dissolve',at:1,dur:0.5});"
            "const c0=T.compose(2.0-1/60,{from:A,to:B,kind:'cut-on-beat',beat:2.0,fps:30,kit:IN.kit}),"
            "c1=T.compose(2.0,{from:A,to:B,kind:'cut-on-beat',beat:2.0,fps:30,kit:IN.kit});"
            "const p=T.compose(1.32,{from:A,to:B,id:'page-push',at:1,kit:IN.fk});"
            "console.log(JSON.stringify({d:d1.elements.map(e=>[e.id,e.layer,+e.alpha.toFixed(4)]), same:JSON.stringify(d1)===JSON.stringify(d2),"
            "c0:c0.elements.map(e=>e.layer), c1:c1.elements.map(e=>e.layer), cstart:c1.start, ckind:c1.kind,"
            "p:p.elements.map(e=>[e.id,e.layer,e.x]), pk:p.kind, pp:p.p}));",
            tmp, {"A": A, "B": B, "kit": ops_rt, "fk": fn_rt})
        r9 = res or {}
        s.check("dissolve cross-fades both scenes as a pure function of t",
                r9.get("same") is True and all(0 < a < 1 for _i, _l, a in r9.get("d", [])), r9.get("d"))
        s.check("cut-on-beat: the cut lands exactly on the beat frame (scene A before, scene B on the beat)",
                set(r9.get("c0", [])) == {"from"} and set(r9.get("c1", [])) == {"to"} and r9.get("cstart") == 2.0
                and r9.get("ckind") == "cut", r9)
        px = {f"{i}:{lay}": x for i, lay, x in r9.get("p", [])}
        s.check("push moves the outgoing scene off and brings the incoming one in along the kit's direction",
                r9.get("pk") == "push" and 0 < r9.get("pp", 0) < 1 and px.get("cache:from", 0) < 100
                and px.get("cache:to", 0) > 900, r9.get("p"))
        res, cp = node("const e=T.pick('page-push',IN.fk);const st=T.startForBeat(4.0,e,IN.fk);"
                       "const s=T.compose(4.0,{from:IN.A,to:IN.B,id:'page-push',beat:4.0,kit:IN.fk});"
                       "const ease=T.easeFor(e,IN.fk);console.log(JSON.stringify({st, p:s.p, start:s.start, dur:s.dur}));",
                       tmp, {"A": A, "B": B, "fk": fn_rt})
        s.check("a transition's reveal lands on the beat (start = beat - reveal x duration; ~90% of the move at the beat)",
                res is not None and res["start"] < 4.0 and abs(res["start"] - res["st"]) < 1e-9
                and 0.88 <= res["p"] <= 0.93, res)
        res, cp = node("const s=T.compose(0.3,{from:IN.A,to:IN.B,id:'paper-fold',kind:'wipe',mask:'wipe-left',at:0,dur:0.6,kit:IN.fk});"
                       "console.log(JSON.stringify({in_kit:s.in_kit, kind:s.kind, notes:s.notes, n:s.elements.length}));",
                       tmp, {"A": A, "B": B, "fk": fn_rt})
        s.check("an invented transition is drawn and reported in_kit false — never refused",
                res is not None and res["in_kit"] is False and res["kind"] == "wipe" and res["n"] == 4
                and any("not in the active kit" in n for n in res["notes"]), res)
        res, cp = node("console.log(JSON.stringify({a0:T.anchorAlpha(0,{}), late:T.anchorState(1.0,{enter:2.4,fadeIn:0.3}),"
                       "lateA:T.anchorAlpha(0.69,{enter:2.4,fadeIn:0.3})}));", tmp, {})
        s.check("first-second anchor: fully visible at frame 0 by default", res is not None and res["a0"] == 1, res)
        s.check("first-second anchor: a late entrance is pulled forward so the anchor is visible by 1.0 s (and noted)",
                res is not None and res["late"]["alpha"] == 1 and res["lateA"] < 1 and res["late"]["note"], res)

        # ---- the continuity gate on real scene_runtime traces
        def trace(anchor_enter: float, name: str):
            out_p = tmp / f"{name}.trace.json"
            res_, cp_ = node(
                "const tr=R.buildTrace({name:IN.name, script:[{name:'l1', text:'hello', start:0, end:3}], grid:10,"
                "draw:(sc,t)=>{ sc.card({id:'title', x:200, y:200, w:900, h:160,"
                " alpha: T.anchorAlpha(t,{enter:IN.enter, fadeIn:0.3})}); }});"
                "require('fs').writeFileSync(IN.out, JSON.stringify(tr)); console.log(JSON.stringify({ok:true}));",
                tmp, {"enter": anchor_enter, "name": name, "out": str(out_p)})
            return out_p if res_ else None

        t_good, t_late = trace(0.0, "good"), trace(1.6, "late")
        # a late enter is pulled forward by anchorAlpha itself — so emulate an un-anchored scene with a raw fade
        res, cp = node(
            "const tr=R.buildTrace({name:'empty', script:[{name:'l1', text:'hello', start:0, end:3}], grid:10,"
            "draw:(sc,t)=>{ sc.card({id:'title', x:200, y:200, w:900, h:160, alpha: Math.max(0,Math.min(1,(t-1.6)/0.3))}); }});"
            "require('fs').writeFileSync(IN.out, JSON.stringify(tr)); console.log(JSON.stringify({ok:true}));",
            tmp, {"out": str(tmp / "empty.trace.json")})
        plan = {"scenes": [{"id": "intro", "start": 0, "elements": ["title", "cache"]},
                           {"id": "how", "transition": "cut", "elements": ["cache", "db"]}]}
        (tmp / "plan_cut.json").write_text(json.dumps(plan), encoding="utf-8")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_cut.json"), "--trace", str(t_good),
                                                            "--kit", str(FIELD)], env)
        s.check("check_continuity flags a cut between scenes sharing an element id (exit 1)",
                r.returncode == 1 and any("CUT" in f and "cache" in f for f in out.get("findings", [])), r.stdout[-400:])
        plan["scenes"][1]["transition"] = "morph"
        (tmp / "plan_morph.json").write_text(json.dumps(plan), encoding="utf-8")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_morph.json"), "--trace", str(t_good),
                                                            "--kit", str(FIELD)], env)
        s.check("check_continuity passes a morph between scenes sharing an element id, with a visible first second",
                r.returncode == 0 and (out.get("first_second", {}).get("first_visible_t") or 9) <= 0.3, r.stdout[-400:])
        plan["scenes"][1].update(transition="cut", cut_reason="a deliberate jump-in to the close-up")
        (tmp / "plan_reason.json").write_text(json.dumps(plan), encoding="utf-8")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_reason.json"), "--trace", str(t_good)], env)
        s.check("a deliberate cut with a stated cut_reason is a note, not a finding",
                r.returncode == 0 and any("deliberate cut" in n for n in out.get("notes", [])), r.stdout[-400:])
        plan["scenes"][1].update(transition="morph")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_morph.json"), "--trace",
                                                            str(tmp / "empty.trace.json")], env)
        s.check("check_continuity flags an empty first second, measured from a scene_runtime trace (exit 1)",
                r.returncode == 1 and any("first 1 s" in f for f in out.get("findings", [])), r.stdout[-400:])
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_morph.json"), "--trace", str(t_late)], env)
        s.check("an anchor built with anchorState/anchorAlpha passes the first-second check even when authored late",
                r.returncode == 0 and (out.get("first_second", {}).get("first_visible_t") or 9) <= 1.0, r.stdout[-400:])
        odd = {"scenes": [{"id": "a", "elements": [{"id": "title", "visible_from": 0.0}]},
                          {"id": "b", "transition": "sparkle-swirl", "elements": ["x"]}]}
        (tmp / "plan_odd.json").write_text(json.dumps(odd), encoding="utf-8")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_odd.json"), "--kit", str(FIELD)], env)
        s.check("a transition outside the kit vocabulary is a note only — never a failure",
                r.returncode == 0 and any("not in the active kit" in n for n in out.get("notes", [])), r.stdout[-400:])
        blind = {"scenes": [{"id": "a", "elements": ["title"]}, {"id": "b", "transition": "morph", "elements": ["title"]}]}
        (tmp / "plan_blind.json").write_text(json.dumps(blind), encoding="utf-8")
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "plan_blind.json")], env)
        s.check("an unmeasurable first second is reported as NOT measured, never passed silently",
                out.get("first_second", {}).get("measured") is False and "NOT measured" in out.get("summary", ""),
                r.stdout[-300:])
        r, out = gate("scripts/gates/check_continuity.py", [str(tmp / "nope.json")], env)
        s.check("check_continuity with no plan is SKIPPED (exit 2)", r.returncode == 2, r.stdout[-200:])

        # ---- DOM caption render in a real browser (when one is available)
        pw_env = dict(env)
        pw_env["PYTHONPATH"] = os.pathsep.join([site.getusersitepackages()] + sys.path)
        cache = Path(os.path.expanduser("~")) / "Library" / "Caches" / "ms-playwright"
        if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            pw_env["PLAYWRIGHT_BROWSERS_PATH"] = os.environ["PLAYWRIGHT_BROWSERS_PATH"]
        elif cache.exists():
            pw_env["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)
        elif (Path(os.path.expanduser("~")) / ".cache" / "ms-playwright").exists():
            pw_env["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(os.path.expanduser("~")) / ".cache" / "ms-playwright")
        page = tmp / "dom.html"
        page.write_text(
            "<!doctype html><html><body style='margin:0;width:1920px;height:1080px;position:relative;background:#17261f'>"
            f"<script>{(RENDER / 'captions.js').read_text(encoding='utf-8')}</script>"
            f"<script>window.__C8_KIT__={json.dumps(kit_rt)};window.__C8_CAPTION_STYLE__={json.dumps(style_rt)};"
            "C8Captions.draw(document.body, 1.0, {cues:[{text:'Keep the ⟦key:hit rate⟧ high and the ⟦bad:misses⟧ rare.',"
            "start:0,end:3}]});document.title='READY';</script></body></html>", encoding="utf-8")
        probe = tmp / "dom_probe.py"
        probe.write_text(
            "import json,sys\nfrom playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n b=p.chromium.launch()\n pg=b.new_page(viewport={'width':1920,'height':1080})\n"
            f" pg.goto({json.dumps(page.resolve().as_uri())})\n"
            " r=pg.evaluate(\"()=>Array.from(document.querySelectorAll('#c8-captions span')).map(s=>({t:s.textContent,"
            "m:s.dataset.mark,fs:parseFloat(getComputedStyle(s).fontSize),c:getComputedStyle(s).color}))\")\n"
            " b.close()\nprint(json.dumps(r))\n", encoding="utf-8")
        cp = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True, env={**os.environ, **pw_env},
                            timeout=90)
        if cp.returncode != 0:
            s.check("no headless browser for the DOM caption probe — SKIPPED (recorded)", True, cp.stderr[-200:])
        else:
            spans = json.loads(cp.stdout.strip().splitlines()[-1])
            acc = chalk_dark["accent"].lstrip("#")
            rgb = f"rgb({int(acc[0:2], 16)}, {int(acc[2:4], 16)}, {int(acc[4:6], 16)})"
            s.check("DOM caption render in a real browser: every word >= 28 px and the key mark painted in the kit "
                    "accent", spans and all(x["fs"] >= 28 for x in spans)
                    and any(x["m"] == "key" and x["c"] == rgb for x in spans), spans)

    # ======================================================================== cover (local, no hosted service)
    scene = tmp / "scene.html"
    scene.write_text(
        "<!doctype html><html><body style='margin:0;background:#17261f'><canvas id=c width=1920 height=1080></canvas>"
        "<script>const c=document.getElementById('c').getContext('2d');window.renderAt=function(t){"
        "c.fillStyle='#17261f';c.fillRect(0,0,1920,1080);c.fillStyle='#f4d35e';c.fillRect(200+100*Math.min(t,3),300,500,300);};"
        "const q=new URLSearchParams(location.search);window.renderAt(parseFloat(q.get('t')||'0'));"
        "fetch('https://example.invalid/x').catch(()=>{});document.title='READY';</script></body></html>",
        encoding="utf-8")
    out_dir = tmp / "covers"
    try:
        pw_env  # noqa: B018 — defined when node ran
    except NameError:
        pw_env = dict(env)
    r = run("scripts/render/cover.py", ["--scene", str(scene), "--t", "3", "--title",
                                        "How the ⟦key:cache⟧ keeps every request under a second",
                                        "--kit", str(CHALK), "--out-dir", str(out_dir), "--name", "demo"],
            env=pw_env, timeout=180)
    try:
        cov = json.loads(r.stdout)
    except Exception:  # noqa: BLE001
        cov = {"status": "ERROR", "reason": (r.stdout + r.stderr)[-300:]}
    if cov.get("status") == "SKIPPED":
        s.check("cover.py skipped honestly: no browser here, reason and unlock recorded (no fake image)",
                bool(cov.get("reason")) and bool(cov.get("unlock")) and not list(out_dir.glob("*.png")), cov)
    else:
        import cover as cover_mod  # noqa: E402
        files = {(f["width"], f["height"]): Path(f["path"]) for f in cov.get("files", [])}
        s.check("cover.py writes a 1280x720 cover PNG (pixel size read from the file header)",
                (1280, 720) in files and cover_mod.png_size(files[(1280, 720)]) == (1280, 720), cov.get("files"))
        s.check("cover.py writes a 1080x1920 cover PNG (pixel size read from the file header)",
                (1080, 1920) in files and cover_mod.png_size(files[(1080, 1920)]) == (1080, 1920), cov.get("files"))
        ms = cov.get("measured", {})
        okm = ms and all(v["checks"]["title_inside_safe_area"] and v["checks"]["title_no_horizontal_overflow"]
                         and v["checks"]["title_at_most_2_lines"] and v["checks"]["title_contrast_worst_case"] >= 4.5
                         and v["checks"]["title_font_px"] >= v["checks"]["title_floor_px"] for v in ms.values())
        s.check("cover title measured in the browser: inside the safe area, <= 2 lines, at or above its floor, "
                ">= 4.5:1 over its scrim", cov.get("status") == "PASS" and okm, {k: v["checks"] for k, v in ms.items()})
        acc = chalk_dark["accent"].lstrip("#")
        rgb = f"rgb({int(acc[0:2], 16)}, {int(acc[2:4], 16)}, {int(acc[4:6], 16)})"
        marks_seen = [mk for v in ms.values() for mk in v["measure"].get("marks", [])]
        s.check("the cover's marked title word is painted in the kit accent", marks_seen and
                all(mk["mark"] == "key" and mk["color"] == rgb for mk in marks_seen), marks_seen)
        s.check("cover rendering is local: every non-file request (the scene's own fetch) was blocked",
                cov.get("network_blocked") and all("example.invalid" in u for u in cov["network_blocked"])
                and cov.get("hosted_services", "").startswith("none"), cov.get("network_blocked"))
    probe = (f"import sys; sys.path.insert(0, {json.dumps(str(RENDER))}); sys.path.insert(0, "
             f"{json.dumps(str(ROOT / 'scripts'))}); import cover; cover._have_playwright=lambda: False; "
             f"cover.find_chrome=lambda: None; import json; print(json.dumps(cover.render_cover(title='x', "
             f"out_dir={json.dumps(str(tmp / 'nob'))})))")
    cp = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env={**os.environ, **env})
    try:
        nob = json.loads(cp.stdout)
    except Exception:  # noqa: BLE001
        nob = {}
    s.check("with no browser, cover.py skips honestly with a reason and the unlock command, writing nothing",
            nob.get("status") == "SKIPPED" and "no headless browser" in nob.get("reason", "")
            and "playwright" in nob.get("unlock", "") and not list((tmp / "nob").glob("*.png")), nob or cp.stderr[-300:])
    src = (RENDER / "cover.py").read_text(encoding="utf-8")
    s.check("cover.py contains no network endpoint or hosted image service (renders a frame locally)",
            not re.search(r"https?://", src), re.findall(r"https?://\S+", src)[:3])

sys.exit(s.finish())
