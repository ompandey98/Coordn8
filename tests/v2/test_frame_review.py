#!/usr/bin/env python3
"""Suite "frame_review" — E6 automatic frame review (contact sheets -> blind reviewer ->
stage_v verdicts) and the H4 blind ranking API.

Builds a 6-second fixture video in a temp dir whose every frame ENCODES ITS OWN INDEX in
two flat grey blocks, so the test can prove from the sheet pixels that each cell shows the
exact frame the beat map asked for. Nothing is left on disk afterwards.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "render"))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

FIX = Path(__file__).resolve().parent / "fixtures" / "frame_review"
FR = "scripts/review/frame_review.py"
W, H, FPS, N = 320, 180, 30, 180
IMG = {".png", ".jpg", ".jpeg", ".ppm", ".webp", ".bmp"}

s = Suite("frame_review")


def have_media() -> bool:
    try:
        import PIL  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_video(path: Path, marker: int) -> None:
    """Frame n: block A = (n % 60) * 4, block B = (n // 60) * 40, block C = marker."""
    from PIL import Image, ImageDraw
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast",
           "-pix_fmt", "yuv444p", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for n in range(N):
        im = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(im)
        a, b = (n % 60) * 4, (n // 60) * 40
        d.rectangle([10, 40, 100, 140], fill=(a, a, a))
        d.rectangle([115, 40, 205, 140], fill=(b, b, b))
        d.rectangle([220, 40, 310, 140], fill=(marker, marker, marker))
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()


def decode_cell(sheet_img, box) -> tuple:
    x, y, w, h = box
    sx, sy = w / float(W), h / float(H)

    def px(cx, cy):
        v = sheet_img.getpixel((int(x + cx * sx), int(y + cy * sy)))
        return sum(v[:3]) / 3.0
    a, b, c = px(55, 90), px(160, 90), px(265, 90)
    return int(round(b / 40.0)) * 60 + int(round(a / 4.0)), c


def images_under(root: Path) -> list:
    return sorted(str(p) for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() in IMG)


def make_build(root: Path, name: str = "b", *, with_claims: bool = True) -> Path:
    b = root / name
    (b / "_build").mkdir(parents=True)
    (b / "video").mkdir()
    for f in ("beatmap.json", "words.json", "storyboard.json"):
        shutil.copy(FIX / f, b / "_build" / f)
    if with_claims:
        shutil.copy(FIX / "claims.json", b / "_build" / "claims.json")
        shutil.copy(FIX / "verification.json", b / "_build" / "verification.json")
    (b / "video" / "s1.js").write_text("// SRC: router.py:40\nscene('s1');\n", encoding="utf-8")
    (b / "video" / "s2.js").write_text("// SRC: engine.py:12\nscene('s2');\n", encoding="utf-8")
    old = time.time() - 30
    for p in list((b / "_build").glob("*.json")) + list((b / "video").glob("*.js")):
        os.utime(p, (old, old))
    make_video(b / "demo.mp4", 0)
    return b


def jrun(args, env):
    cp = run(FR, args, env=env, timeout=170)
    payload = None
    for line in (cp.stdout or "").splitlines():
        if line.strip().startswith("{"):
            try:
                payload = json.loads(line)
            except ValueError:
                pass
    return cp.returncode, (payload or {}), cp


def fill(template: dict, answers: dict, contradict_slot: str | None = None,
         drop_slot: str | None = None, paraphrase_slot: str | None = None) -> dict:
    t = copy.deepcopy(template)
    vs = []
    for v in t["verdicts"]:
        if v["slot"] == drop_slot:
            continue
        key = "contradicts" if v["slot"] == contradict_slot else "match"
        v.update(answers[key])
        if v["slot"] == paraphrase_slot:
            v["sentence"] = "Something else entirely is said here."
        vs.append(v)
    t["verdicts"] = vs
    for e in t["events"]:
        e["ok"], e["note"] = True, "settled"
    return t


# --------------------------------------------------------------------- pure planning
import contact_sheet as cs  # noqa: E402
import blind_brief as bb  # noqa: E402
import frame_review as frv  # noqa: E402

bm = json.loads((FIX / "beatmap.json").read_text(encoding="utf-8"))
sb = json.loads((FIX / "storyboard.json").read_text(encoding="utf-8"))
words = json.loads((FIX / "words.json").read_text(encoding="utf-8"))
pl = cs.plan(bm, fps=FPS, frames=N, duration=N / FPS, storyboard=sb, words=words)
s.check("plan: one scene per storyboard scene (s1, s2)",
        [sc["id"] for sc in pl["scenes"]] == ["s1", "s2"], [sc["id"] for sc in pl["scenes"]])
lines = [ln for sc in pl["scenes"] for ln in sc["lines"]]
win_ok = all(ln["start"] + 0.85 * (ln["end"] - ln["start"]) - 1e-6 <= ln["frame"] / FPS
             <= ln["start"] + 0.90 * (ln["end"] - ln["start"]) + 1e-6 for ln in lines)
s.check("plan: every spoken line sampled 85-90% through the line",
        win_ok and len(lines) == 4, [(ln["frame"], ln["start"], ln["end"]) for ln in lines])
s.check("plan: line frames are the exact beat-map-derived indices (37, 82, 127, 170)",
        [ln["frame"] for ln in lines] == [37, 82, 127, 170], [ln["frame"] for ln in lines])
cuts = [sc["cut"]["frame"] for sc in pl["scenes"]]
s.check("plan: 4 frames before each cut (scene s1 cuts at 3.1s -> f89; s2 at end -> f176)",
        cuts == [89, 176], cuts)
hitf = [(h["hit"], h["offset"], h["frame"]) for sc in pl["scenes"] for h in sc["hits"]]
s.check("plan: -16/-3/+2 frames around each hit (t-anchored and word-anchored)",
        hitf == [("hit-miss", -16, 98), ("hit-miss", -3, 111), ("hit-miss", 2, 116),
                 ("hit-written", -16, 137), ("hit-written", -3, 150), ("hit-written", 2, 155)], hitf)
bad_words = copy.deepcopy(words)
bad_words["lines"][0]["words"] = [w for w in bad_words["lines"][0]["words"] if w["w"] != "written"]
pl_bad = cs.plan(bm, fps=FPS, frames=N, duration=N / FPS, storyboard=sb, words=bad_words)
s.check("plan: a hit on a word that is no longer in its line is an error, never a guess",
        any("written" in e for e in pl_bad["errors"]), pl_bad["errors"])
s.check("plan: the reviewer sees the display sentence (caption markup stripped)",
        lines[1]["sentence"] == "It checks the cache first.", lines[1]["sentence"])

# --------------------------------------------------------------------- blind guard (unit)
claims_demo = ["The hero frame shows the cache hit rate at ninety four percent",
               "A miss goes on to the engine."]
leak = bb.find_leaks("brief ... The hero frame shows the cache hit rate at ninety four percent ...",
                     claims_demo, ["A miss goes on to the engine."])
s.check("blind guard: a builder claim planted in a brief is detected as a leak",
        leak == [claims_demo[0]], leak)
s.check("blind guard: a claim that IS a spoken sentence is not a false leak",
        bb.find_leaks("> A miss goes on to the engine.", claims_demo,
                      ["A miss goes on to the engine."]) == [], "")

# --------------------------------------------------------------------- skill file
skill = ROOT / "skills" / "coordn8-frame-review" / "SKILL.md"
txt = skill.read_text(encoding="utf-8") if skill.is_file() else ""
m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.S)
fm = {}
if m:
    for ln in m.group(1).splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
body = m.group(2) if m else ""
s.check("skill: SKILL.md parses with frontmatter name coordn8-frame-review and a description",
        fm.get("name") == "coordn8-frame-review" and 20 < len(fm.get("description", "")) <= 1536, fm)
s.check("skill: body names the coordn8-frame-reviewer agent and says the step is automatic",
        "coordn8-frame-reviewer" in body and re.search(r"automatic", body, re.I)
        and re.search(r"after every render", body, re.I), body[:200])
s.check("skill: body runs prepare -> blind dispatch -> verdicts -> stage_v check, blocks on contradicts, "
        "and has the degraded no-subagent path",
        all(k in body for k in ("frame_review.py prepare", "stage_v.py check", "contradicts",
                                "verdicts.json", "frame_review.py collect"))
        and re.search(r"without subagents|no subagent", body, re.I), "")

# --------------------------------------------------------------------- end to end
if not have_media():
    s.check("e2e: SKIPPED — ffmpeg/ffprobe or Pillow missing (frame review cannot run here)", True,
            "skip recorded")
    sys.exit(s.finish())

from PIL import Image  # noqa: E402

with tmpdir() as td:
    root = Path(td)
    env = sandbox_env(root / "sbx")
    # HOME is sandboxed, which hides per-user site-packages (where Pillow may live);
    # keep the interpreter's real import path so the subprocess sees the same libraries.
    import site
    env["PYTHONPATH"] = os.pathsep.join([p for p in sys.path if p and Path(p).is_dir()]
                                        + [site.getusersitepackages()])
    b = make_build(root)
    before_imgs = images_under(root)

    rc, out, cp = jrun(["prepare", str(b)], env)
    s.check("prepare: exits 0 and reports PASS", rc == 0 and out.get("status") == "PASS",
            (rc, out.get("summary"), cp.stderr[-300:]))
    review = b / "_build" / "review"
    man = json.loads((review / "manifest.json").read_text(encoding="utf-8")) if (review / "manifest.json").is_file() else {}
    sheets = man.get("sheets", [])
    s.check("prepare: one contact sheet per scene", sorted(sh["scene"] for sh in sheets) == ["s1", "s2"]
            and all(Path(sh["path"]).is_file() for sh in sheets), [sh.get("path") for sh in sheets])
    mlines = man.get("lines", {})
    s.check("prepare: manifest times match the beat map (lines 85-90%, cut-4, hit -16/-3/+2)",
            [mlines[k]["frame"] for k in sorted(mlines)] == [37, 82, 127, 170]
            and [sc["cut"]["frame"] for sc in man["plan"]["scenes"]] == [89, 176]
            and [h["frame"] for sc in man["plan"]["scenes"] for h in sc["hits"]]
            == [98, 111, 116, 137, 150, 155], mlines)

    # pixel proof: every cell shows exactly the frame the manifest names
    mism = []
    for sh in sheets:
        im = Image.open(sh["path"]).convert("RGB")
        for c in sh["cells"]:
            if c["box"]:
                got, _ = decode_cell(im, c["box"])
                if got != c["frame"]:
                    mism.append((c["cell"], c["frame"], got))
        im.close()
    ncells = sum(1 for sh in sheets for c in sh["cells"] if c["box"])
    s.check("prepare: every sheet cell shows exactly the frame it is labelled with (decoded from pixels)",
            not mism and ncells == 4 + 2 + 6, (mism, ncells))

    leftover = sorted(set(images_under(root)) - set(before_imgs) - {sh["path"] for sh in sheets})
    s.check("prepare: zero frame files left on disk — only the sheets were written",
            not leftover and man.get("frames_left_on_disk") == 0, leftover)

    brief_md = (review / "blind_brief.md").read_text(encoding="utf-8")
    brief_all = brief_md + (review / "blind_brief.json").read_text(encoding="utf-8") \
        + (review / "verdicts.template.json").read_text(encoding="utf-8")
    sentences = [cs.display_text(x["text"]) for x in bm["sentences"]]
    s.check("blind brief: contains every exact spoken sentence",
            all(x in brief_md for x in sentences), [x for x in sentences if x not in brief_md])
    seeded = []
    for f in ("claims.json", "verification.json", "storyboard.json"):
        seeded += bb._strings(json.loads((FIX / f).read_text(encoding="utf-8")))
    seeded = [x for x in seeded if len(x) >= 12 and " " in x]
    leaked = [x for x in seeded if bb._norm(x) in bb._norm(brief_all)]
    s.check("blind brief: none of the seeded builder claims (claims, verification, storyboard) appear",
            len(seeded) >= 8 and not leaked and man["blind"]["claims_scanned"] >= 8, leaked)
    s.check("blind brief: names the reviewer agent, the sheets and the Limits block",
            "coordn8-frame-reviewer" in brief_md and all(sh["path"] in brief_md for sh in sheets)
            and "non-Anthropic hosted AI service" in brief_md, "")

    tpl = json.loads((review / "verdicts.template.json").read_text(encoding="utf-8"))
    answers = json.loads((FIX / "answers.json").read_text(encoding="utf-8"))
    s.check("template: one verdict slot per spoken line, pre-filled with the exact sentence",
            sorted(v["line_idx"] for v in tpl["verdicts"]) == [1, 2, 3, 4]
            and all(v["sentence"] in sentences for v in tpl["verdicts"]), tpl["verdicts"][:1])

    rc, out, _ = jrun(["status", str(b)], env)
    s.check("status: before verdicts the review is PENDING (open item, never a pass)",
            rc == 2 and out.get("status") == "PENDING", out)

    vpath = review / "verdicts.json"
    vpath.write_text(json.dumps(fill(tpl, answers)), encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    res_file = review / "stage_v.json"
    s.check("collect: all-match verdicts -> PASS (exit 0) and stage_v.json written",
            rc == 0 and out.get("status") == "PASS" and res_file.is_file()
            and json.loads(res_file.read_text())["status"] == "PASS", (rc, out.get("summary")))
    svp = b / "_build" / "stage_v" / "verdicts.json"
    cp2 = run("scripts/gates/stage_v.py", ["check", str(svp)], env=env)
    s.check("collect: writes _build/stage_v/verdicts.json that stage_v.py check passes (orchestrate stage 5 input)",
            svp.is_file() and cp2.returncode == 0, cp2.stdout[-300:])
    rc, out, _ = jrun(["status", str(b)], env)
    s.check("status: after a clean review -> PASS", rc == 0 and out.get("status") == "PASS", out)

    vpath.write_text("Here is my review:\n```json\n" + json.dumps(fill(tpl, answers, contradict_slot="s2/L3"))
                     + "\n```\n", encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: one `contradicts` verdict -> BLOCK (non-zero exit)",
            rc != 0 and out.get("status") == "BLOCK" and out.get("blocking"), (rc, out.get("summary")))
    s.check("collect: the reviewer's fenced-JSON reply is accepted verbatim",
            out.get("slots_answered") == 4, out.get("slot_problems"))

    native = fill(tpl, answers)
    for v in native["verdicts"]:
        v.pop("slot"), v.pop("line_idx")
    vpath.write_text(json.dumps(native), encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: the reviewer agent's native reply (frame + sentence, no slot ids) maps to every slot",
            rc == 0 and out.get("status") == "PASS" and out.get("slots_answered") == 4, out.get("summary"))

    vpath.write_text(json.dumps({"refused": "stale artefact", "freshness_problems": ["old"]}),
                     encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: a reviewer refusal (stale artefact) is never a pass",
            rc != 0 and out.get("status") == "STALE", out.get("summary"))

    vpath.write_text(json.dumps(fill(tpl, answers, drop_slot="s1/L2")), encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: a spoken line with no verdict slot answered -> FAIL naming the slot",
            rc != 0 and out.get("status") == "FAIL" and out.get("missing_slots") == ["s1/L2"], out)

    vpath.write_text(json.dumps(fill(tpl, answers, paraphrase_slot="s1/L1")), encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: a verdict for a paraphrased (not the exact) sentence -> FAIL",
            rc != 0 and out.get("status") == "FAIL"
            and any("exact spoken sentence" in p for p in out.get("slot_problems", [])), out)

    bad = fill(tpl, answers)
    bad["verdicts"][0].pop("large_dead_region")
    vpath.write_text(json.dumps(bad), encoding="utf-8")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("collect: an unanswered schema field is NOT verified (REVIEW, non-zero)",
            rc != 0 and out.get("status") == "REVIEW" and out.get("incomplete"), out.get("summary"))

    # freshness: a scene source edited after the sheets -> refused
    vpath.write_text(json.dumps(fill(tpl, answers)), encoding="utf-8")
    future = time.time() + 120
    os.utime(b / "video" / "s2.js", (future, future))
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("freshness: a sheet older than a scene source is refused (STALE)",
            rc != 0 and out.get("status") == "STALE", out.get("summary"))
    rc, out, _ = jrun(["prepare", str(b)], env)
    s.check("freshness: prepare refuses a video older than a scene source (re-render first)",
            rc != 0 and out.get("status") == "STALE", out.get("summary"))
    old = time.time() - 30
    os.utime(b / "video" / "s2.js", (old, old))
    rc, out, _ = jrun(["prepare", str(b)], env)
    sh0 = Path(json.loads((review / "manifest.json").read_text())["sheets"][0]["path"])
    with open(sh0, "ab") as fh:
        fh.write(b"\0")
    rc, out, _ = jrun(["collect", str(vpath)], env)
    s.check("freshness: a sheet modified after prepare (hash mismatch) is refused",
            rc != 0 and out.get("status") == "STALE" and "hash" in json.dumps(out), out.get("summary"))

    # previous-cut pairing
    prev = root / "prev"
    (prev / "_build").mkdir(parents=True)
    shutil.copy(FIX / "prev_beatmap.json", prev / "_build" / "beatmap.json")
    make_video(prev / "old.mp4", 250)
    rc, out, _ = jrun(["prepare", str(b), "--prev", str(prev)], env)
    man = json.loads((review / "manifest.json").read_text(encoding="utf-8"))
    ml = man["lines"]
    s.check("prev cut: lines are paired by name with the previous cut's own timing",
            rc == 0 and ml["s1/L2"]["prev"]["frame"] == 56 and ml["s1/L1"]["prev"]["frame"] == 37
            and ml["s2/L4"].get("prev") is None and "new line" in ml["s2/L4"]["prev_status"],
            {k: (v.get("prev"), v.get("prev_status")) for k, v in ml.items()})
    pm = []
    for sh in man["sheets"]:
        im = Image.open(sh["path"]).convert("RGB")
        for c in sh["cells"]:
            if c["src"] == "prev" and c["box"]:
                got, marker = decode_cell(im, c["box"])
                if got != c["frame"] or marker < 200:
                    pm.append((c["cell"], c["frame"], got, marker))
            if c["src"] == "now" and c["box"]:
                _, marker = decode_cell(im, c["box"])
                if marker > 50:
                    pm.append((c["cell"], "now cell shows the old cut"))
        im.close()
    nprev = sum(1 for sh in man["sheets"] for c in sh["cells"] if c["src"] == "prev")
    s.check("prev cut: the previous cut's frame sits beside the new one (pixels prove which video)",
            not pm and nprev == 4, pm)
    brief_md = (review / "blind_brief.md").read_text(encoding="utf-8")
    s.check("prev cut: the brief asks 'what used to be here?' with the BEFORE cells",
            "what used to be here" in brief_md.lower() and "Previous cut beside it: yes" in brief_md, "")
    leftover = sorted(set(images_under(root)) - set(before_imgs)
                      - {sh["path"] for sh in man["sheets"]} - set(images_under(prev)))
    s.check("prev cut: still zero frame files left on disk", not leftover, leftover)

    rc, out, _ = jrun(["clean", str(b)], env)
    s.check("clean: removes the sheets, leaving zero images in the build",
            rc == 0 and not images_under(b) and (review / "manifest.json").is_file(), images_under(b))

    # H4 ranking API
    vd = root / "variants" / "opening"
    for i, name in enumerate(("v1", "v2", "v3")):
        (vd / name).mkdir(parents=True)
        Image.new("RGB", (64, 36), (40 * i, 0, 0)).save(vd / name / "sheet.jpg")
    (vd / "sentence.txt").write_text("The request arrives at the router.\n", encoding="utf-8")
    r = frv.prepare_variants(vd)
    rb = Path(r.get("brief", "")).read_text(encoding="utf-8") if r.get("brief") else ""
    s.check("rank: prepare_variants writes a blind brief of anonymised candidates with the sentence",
            r.get("status") == "PASS" and "candidate_A" in rb and "The request arrives" in rb
            and not re.search(r"\bv[123]\b", rb), rb[:300])
    s.check("rank: rank() returns [] while the blind reviewer has not ranked (never a fake order)",
            frv.rank(vd) == [], "")
    labels = json.loads((vd / "review" / ".label_map.json").read_text())["labels"]
    rk = (FIX / "ranking.json").read_text(encoding="utf-8")
    rk = rk.replace("@BEST", labels["v2"]).replace("@WORST", labels["v1"]).replace("@MID", labels["v3"])
    (vd / "review" / "ranking.json").write_text(rk, encoding="utf-8")
    ranked = frv.rank(vd)
    s.check("rank: maps blind labels back to variants; a contradicting treatment sinks to last",
            [x["variant"] for x in ranked] == ["v2", "v3", "v1"] and ranked[-1]["disqualified"]
            and all(len(x["reason"]) >= 10 for x in ranked), ranked)
    rj = json.loads((vd / "review" / "ranked.json").read_text())
    s.check("rank: review/ranked.json keeps the best plus the alternates with the reviewer's reasons",
            rj["best"] == "v2" and rj["alternates"] == ["v3", "v1"] and rj["blind"] is True, rj)
    broken = json.loads(rk)
    broken["ranking"] = broken["ranking"][:2]
    (vd / "review" / "ranking.json").write_text(json.dumps(broken), encoding="utf-8")
    try:
        frv.rank(vd)
        raised = False
    except ValueError:
        raised = True
    s.check("rank: a ranking that skips a candidate is rejected", raised, "")

nothing_left = not Path(td).exists()
s.check("hermetic: the temp tree (video, sheets, everything) is gone after the suite", nothing_left, td)
sys.exit(s.finish())
