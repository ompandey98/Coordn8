#!/usr/bin/env python3
"""Style distillation (A4) and brand onboarding + saved profiles (A5).

Proves: a synthetic <= 5 s fixture video (made here with ffmpeg lavfi, never committed) distils
to a draft kit whose accent differs from its background family by >= 20 CIEDE2000, with a
contact sheet, cut points, motion per second and type estimates; the same input gives a
byte-identical kit (and the numpy and pure-Python paths agree); image and folder inputs work;
without ffmpeg a PNG still distils and a video is SKIPPED with the reason; near-identical
clusters are merged by CIEDE2000; a grey-only reference still gets a qualifying accent. For
brands: stated colours round-trip through save/load byte-identically; a failing brand colour
(#ffff00 text on white) is adjusted and the adjustment reported; a passing brand colour is kept
exactly; apply is refused without --apply and the design system is untouched; profiles land only
in COORDN8_HOME/profiles; URL intake reads ONE local page (a localhost server, never the
internet) and skips cleanly when unreachable; folder intake maps tokens; every palette passes
the palette gate. Hermetic: temp dirs, sandboxed COORDN8_HOME/CLAUDE_HOME/HOME.
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, PY, Suite, run, sandbox_env, tmpdir  # noqa: E402

s = Suite("distill_brand")
TMP = tmpdir()
T = Path(TMP.name)
ENV = sandbox_env(T)
os.environ.update(ENV)
sys.path.insert(0, str(ROOT / "scripts"))

from design import brand as br  # noqa: E402
from design import distill as dl  # noqa: E402
from design import pick_kit as pk  # noqa: E402
from design.color import delta_e, hue_distance, rgb_to_oklch, contrast  # noqa: E402

FIX = ROOT / "tests" / "v2" / "fixtures" / "distill"
DISTILL = "scripts/design/distill.py"
BRAND = "scripts/design/brand.py"
GATE = "scripts/gates/check_palette.py"
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
NO_FF_PATH = "/usr/bin:/bin"
PAPER, INK, ACCENT, SLATE = "#f4f1ea", "#1f2328", "#e4572e", "#1d2733"


def jline(r) -> dict:
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def gate_on_palette(pal: dict, where: Path) -> int:
    where.mkdir(parents=True, exist_ok=True)
    p = where / "design_system.json"
    p.write_text(json.dumps({"schema": 1, "palette": pal}), encoding="utf-8")
    return run(GATE, [p]).returncode


def make_video(path: Path) -> bool:
    """4 s, 320x180: 2.5 s ivory page with title/body 'glyph' rows and a moving vermilion block,
    then a hard cut to 1.5 s of slate with a small teal panel."""
    hx = lambda c: "0x" + c[1:]  # noqa: E731
    g = "".join(f",drawbox=x={40 + i * 12}:y=24:w=8:h=14:color={hx(INK)}:t=fill" for i in range(12))
    b = "".join(f",drawbox=x={40 + i * 9}:y={70 + ln * 14}:w=5:h=7:color={hx(INK)}:t=fill"
                for ln in range(3) for i in range(20))
    a = (f"color=c={hx(PAPER)}:s=320x180:d=2.5:r=25{g}{b}[bg];color=c={hx(ACCENT)}:s=40x40:d=2.5:r=25[box];"
         f"[bg][box]overlay=x='200+t*30':y=110")
    c = f"color=c={hx(SLATE)}:s=320x180:d=1.5:r=25,drawbox=x=40:y=60:w=60:h=30:color=0x2a9d8f:t=fill"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", a, "-f", "lavfi", "-i", c,
                        "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", str(path)], capture_output=True, text=True, timeout=120)
    return r.returncode == 0 and path.exists()


def png_size(p: Path):
    d = p.read_bytes()
    return struct.unpack(">II", d[16:24]) if d[:8] == b"\x89PNG\r\n\x1a\n" else None


W = T / "work"
W.mkdir()

# ===================================================================== A4 — distillation
VID = W / "ref.mp4"
have_video = bool(FFMPEG) and make_video(VID)
dur = None
if have_video:
    pr = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(VID)],
                        capture_output=True, text=True)
    dur = float(pr.stdout.strip() or 0)
s.check("fixture video is synthesised with ffmpeg lavfi in the test, <= 5 s, nothing committed",
        (have_video and dur is not None and dur <= 5.0) or not FFMPEG,
        f"ffmpeg={bool(FFMPEG)} made={have_video} duration={dur}")
if not FFMPEG:
    s.check("SKIP video distillation: ffmpeg/ffprobe not on this host (recorded reason)", True, "no ffmpeg")

if have_video:
    k1p, k2p, k3p, sheet = W / "k1.json", W / "k2.json", W / "k3.json", W / "sheet.png"
    r1 = run(DISTILL, [VID, "--out", k1p, "--sheet", sheet])
    res1 = jline(r1)
    kit = json.loads(k1p.read_text(encoding="utf-8")) if k1p.exists() else {}
    d = kit.get("distilled") or {}
    s.check("fixture video -> draft kit written, status PASS", r1.returncode == 0 and bool(kit), r1.stderr[-300:])
    fam = d.get("background_family") or []
    acc = (d.get("accent") or {}).get("hex")
    de_bg = min(delta_e(acc, f) for f in fam) if acc and fam else 0
    s.check("accent differs from the background family by >= 20 CIEDE2000 (recomputed independently)",
            de_bg >= 20, f"accent={acc} family={fam} min ΔE={de_bg:.1f}")
    pal = kit.get("palette") or {}
    kde = [delta_e(pal[k]["accent"], pal[k]["paper"]) for k in ("roles", "dark") if pal.get(k)]
    s.check("the kit's own accent is >= 20 CIEDE2000 from its paper in both skins",
            len(kde) == 2 and min(kde) >= 20, kde)
    s.check("separate accent pass finds the small vermilion block that area-ranking buries",
            acc is not None and delta_e(acc, ACCENT) < 10
            and all(p["share"] < 0.1 for p in d.get("palette_clusters", []) if delta_e(p["hex"], ACCENT) < 10),
            f"accent={acc}; clusters={[(p['hex'], p['share']) for p in d.get('palette_clusters', [])]}")
    s.check("k-means palette has <= 8 clusters, shares sum to 1, background is the largest cluster",
            0 < len(d.get("palette_clusters", [])) <= 8
            and abs(sum(p["share"] for p in d["palette_clusters"]) - 1) < 0.01
            and delta_e(d["palette_clusters"][0]["hex"], PAPER) < 5, d.get("palette_clusters"))
    sz = png_size(sheet) if sheet.exists() else None
    shots = (d.get("rhythm") or {}).get("shots") or []
    s.check("contact sheet PNG written: 4-column grid, one cell per keyframe, palette strip",
            sz is not None and sz[0] == 4 * dl.CELL_W and sz[1] == dl.CELL_H * max(1, -(-len(shots) // 4)) + 40
            and (res1.get("sheet") or {}).get("cells") == len(shots), f"size={sz} shots={len(shots)} {res1.get('sheet')}")
    cuts = (d.get("rhythm") or {}).get("cut_points") or []
    s.check("cut point found at the hard cut (2.5 s +- 0.2) and nowhere else", len(cuts) == 1 and abs(cuts[0] - 2.5) <= 0.2,
            cuts)
    mps = (d.get("rhythm") or {}).get("motion_per_second") or []
    s.check("motion per second: one 0..1 value per second of video", len(mps) == -(-int(dur * 100) // 100)
            and all(0 <= v <= 1 for v in mps), mps)
    ty = d.get("type") or {}
    s.check("type sizes estimated: title ~91 px and body ~46 px at 1080p (+-20%), title > body",
            ty.get("found") and abs(ty["title_px"] - 91) <= 18 and abs(ty["body_px"] - 46) <= 9
            and ty["title_px"] > ty["body_px"], ty)
    sc = (kit.get("theme") or {}).get("scale") or {}
    s.check("draft kit type scale follows the estimate, ordered, every size >= 28 px",
            sc and min(sc.values()) >= 28 and sc["title"] >= sc["h1"] >= sc["h2"] >= sc["body"] >= sc["caption"]
            and abs(sc["body"] - ty.get("body_px", 0)) <= 2, sc)
    errs = pk.validate_kit(kit)
    s.check("draft kit passes the kit outcome rules (pick_kit.validate_kit)", errs == [], errs[:3])
    s.check("draft kit palette passes the palette gate (check_palette exit 0)",
            gate_on_palette(pal, W / "ds1") == 0, pal)
    s.check("draft kit is an invented kit (freedom doctrine) started from a shipped kit, with the reason",
            str(kit.get("palette_family", "")).startswith("invented:") and (d.get("started_from") or {}).get("kit")
            and "starting point" in d["started_from"]["why"], d.get("started_from"))
    s.check("draft kit records the rule 'match the style, never copy the content'",
            d.get("rule") == "match the style, never copy the content" and any("never reuse" in x for x in kit.get("dont", [])),
            d.get("rule"))
    blob = k1p.read_text(encoding="utf-8")
    s.check("kit holds no source path or frame data (only content hashes of the reference)",
            str(W) not in blob and str(T) not in blob and "ref.mp4" not in blob and len(blob) < 60000
            and all(len(src.get("sha256_16", "")) == 16 for src in d.get("sources", [])), len(blob))
    r2 = run(DISTILL, [VID, "--out", k2p])
    s.check("same input twice -> byte-identical kit JSON", r2.returncode == 0 and k1p.read_bytes() == k2p.read_bytes(),
            "differs")
    try:
        import numpy  # noqa: F401
        has_np = True
    except Exception:
        has_np = False
    r3 = run(DISTILL, [VID, "--out", k3p], env={"COORDN8_NO_NUMPY": "1"})
    s.check("without numpy the pure-Python path gives the byte-identical kit" + ("" if has_np else " (numpy absent: both pure)"),
            r3.returncode == 0 and k3p.exists() and k1p.read_bytes() == k3p.read_bytes(), r3.stderr[-200:])
    rn = run(DISTILL, [VID], env={"PATH": NO_FF_PATH})
    rj = jline(rn)
    s.check("without ffmpeg a video is SKIPPED (exit 2) with the reason, no traceback",
            rn.returncode == 2 and rj.get("status") == "SKIPPED" and "ffmpeg" in rj.get("summary", "")
            and "Traceback" not in rn.stderr, rn.stdout[-300:] + rn.stderr[-200:])
    # image + folder inputs
    img = W / "still.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(VID), "-frames:v", "1", str(img)],
                   capture_output=True)
    ri = run(DISTILL, [img, "--out", W / "ki.json"])
    ki = json.loads((W / "ki.json").read_text()) if (W / "ki.json").exists() else {}
    s.check("image input -> draft kit with a qualifying accent; motion kept from the start kit (still input)",
            ri.returncode == 0 and ki and delta_e(ki["distilled"]["accent"]["hex"], ACCENT) < 10
            and "still input" in ki["distilled"]["motion_signature"], ri.stdout[-300:])
    fold = W / "refs"
    (fold / "sub").mkdir(parents=True)
    shutil.copy(VID, fold / "a.mp4")
    shutil.copy(img, fold / "sub" / "b.png")
    rf = run(DISTILL, [fold, "--out", W / "kf.json", "--sheet", W / "fs.png"])
    kf = json.loads((W / "kf.json").read_text()) if (W / "kf.json").exists() else {}
    s.check("folder input -> one kit from every video and image inside (2 sources), contact sheet written",
            rf.returncode == 0 and len(kf.get("distilled", {}).get("sources", [])) == 2 and (W / "fs.png").exists(),
            rf.stdout[-300:])
    rnf = run(DISTILL, [fold, "--out", W / "knf.json"], env={"PATH": NO_FF_PATH})
    knf = json.loads((W / "knf.json").read_text()) if (W / "knf.json").exists() else {}
    s.check("without ffmpeg a folder still distils its PNG (pure-Python decoder) and lists the skipped video",
            rnf.returncode == 0 and len(knf.get("distilled", {}).get("sources", [])) == 1
            and any("ffmpeg" in x["why"] for x in knf["distilled"]["skipped"])
            and delta_e(knf["distilled"]["accent"]["hex"], ACCENT) < 10, rnf.stdout[-300:])

# pure-Python units (no ffmpeg needed)
near = [((0.95, 0.002, 0.01), 500), ((0.951, 0.0025, 0.011), 450), ((0.5, 0.15, 0.05), 50)]
merged, n_merges = dl.dedupe(near)
s.check("CIEDE2000 de-duplication merges near-identical clusters (ΔE < 5) and keeps distinct ones",
        len(merged) == 2 and n_merges == 1 and merged[0][1] == 950, merged)
pts = [dl.oklab((r, g, b)) for r in (10, 120, 240) for g in (30, 200) for b in (50, 220) for _ in range(5)]
s.check("seeded k-means++ is deterministic: same seed -> same clusters",
        dl.kmeans(pts, 8, 3) == dl.kmeans(pts, 8, 3), "differs")
grey = bytearray()
for y in range(90):
    for x in range(160):
        v = 245 if (x // 20 + y // 15) % 3 else 60
        grey += bytes((v, v, v))
gp = W / "grey.png"
dl.write_png(gp, 160, 90, bytes(grey))
rg = run(DISTILL, [gp, "--out", W / "kg.json"], env={"PATH": NO_FF_PATH})
kg = json.loads((W / "kg.json").read_text()) if (W / "kg.json").exists() else {}
gd = kg.get("distilled", {})
s.check("grey-only reference: accent is synthesised (said so) and still >= 20 CIEDE2000 from the background",
        rg.returncode == 0 and gd.get("accent", {}).get("source") == "synthesised"
        and gd["accent_check"]["measured_vs_background_family_de"] >= 20
        and pk.validate_kit(kg) == [], rg.stdout[-300:])
s.check("pure-Python PNG writer/decoder round-trip is exact", dl.read_png(gp.read_bytes()) == (160, 90, bytes(grey)),
        "mismatch")

# ===================================================================== A5 — brand profiles
HOME_C8 = Path(ENV["COORDN8_HOME"])
PROF = HOME_C8 / "profiles"
before = {p for p in T.rglob("*")}
r = run(BRAND, ["colours", "paper=#ffffff", "ink=#ffff00", "accent=#0a66c2", "--name", "Acme", "--slug", "acme",
                "--font", "display=Fixture Display", "--save"], env=ENV)
res = jline(r)
pfile = PROF / "acme.json"
s.check("stated colours -> profile saved at COORDN8_HOME/profiles/acme.json",
        r.returncode == 0 and pfile.exists() and (res.get("profile") or {}).get("path") == str(pfile), r.stdout[-300:])
prof = json.loads(pfile.read_text()) if pfile.exists() else {}
adj = [a for a in prof.get("adjustments", []) if a.get("role") == "ink" and a.get("skin") == "light"]
s.check("failing brand colour (#ffff00 text on white) is adjusted to >= 4.5:1 and the adjustment reported",
        adj and adj[0]["from"] == "#ffff00" and adj[0]["to_ratio"] >= 4.5 and adj[0]["from_ratio"] < 4.5
        and prof["palette"]["roles"]["ink"] == adj[0]["to"] and contrast(adj[0]["to"], "#ffffff") >= 4.5
        and any("#ffff00" in x for x in res.get("adjusted_brand_colours", [])), adj or res.get("adjustments"))
s.check("the adjustment changes only OKLCH lightness (hue kept within 2 degrees)",
        adj and hue_distance(rgb_to_oklch(adj[0]["to"])[2], rgb_to_oklch("#ffff00")[2]) <= 2
        and "delta_L_oklch" in adj[0], adj)
s.check("a brand colour that passes is kept exactly (accent #0a66c2, paper #ffffff)",
        prof.get("palette", {}).get("roles", {}).get("accent") == "#0a66c2"
        and prof["palette"]["roles"]["paper"] == "#ffffff", prof.get("palette", {}).get("roles"))
s.check("brand palette passes the palette gate in both skins (check_palette exit 0)",
        prof and gate_on_palette(prof["palette"], W / "ds_brand") == 0 and prof["checks"]["palette_gate"] == "PASS",
        prof.get("checks"))
s.check("brand font stack is made offline-safe (generic family appended) and the fix is reported",
        prof.get("fonts", {}).get("display") == "Fixture Display, sans-serif" and prof.get("font_notes"), prof.get("fonts"))
rl = run(BRAND, ["load", "acme"], env=ENV)
s.check("load prints the saved profile byte-for-byte", rl.returncode == 0 and rl.stdout.encode() == pfile.read_bytes(),
        rl.stderr[-200:])
loaded = br.load_profile("acme")
s.check("save -> load -> serialise is byte-identical (round-trip)", br.serialise(loaded) == pfile.read_bytes(), "differs")
st = pfile.stat().st_mtime_ns
p2, how = br.save_profile(loaded)
s.check("re-saving a loaded profile leaves the file byte-identical", how == "unchanged" and pfile.stat().st_mtime_ns == st
        and br.serialise(loaded) == pfile.read_bytes(), how)
r2b = run(BRAND, ["colours", "paper=#ffffff", "ink=#ffff00", "accent=#0a66c2", "--name", "Acme", "--slug", "acme",
                  "--font", "display=Fixture Display", "--save"], env=ENV)
s.check("the same stated colours give the same profile bytes again (deterministic)",
        r2b.returncode == 0 and (jline(r2b).get("profile") or {}).get("status") == "unchanged", r2b.stdout[-200:])
r3b = run(BRAND, ["colours", "accent=#c2410a", "--slug", "acme", "--save"], env=ENV)
s.check("saving a DIFFERENT profile over an existing slug is refused without --overwrite",
        r3b.returncode == 3 and "--overwrite" in jline(r3b).get("summary", "") and br.serialise(loaded) == pfile.read_bytes(),
        r3b.stdout[-200:])
rbad = run(BRAND, ["colours", "#0a66c2", "--slug", "../evil", "--save"], env=ENV)
s.check("a path-like slug ('../evil') is rejected and nothing is written outside the profile library",
        rbad.returncode == 3 and not (HOME_C8 / "evil.json").exists() and not (T / "evil.json").exists(), rbad.stdout[-200:])
after = {p for p in T.rglob("*")} - before
outside = sorted(str(p.relative_to(T)) for p in after
                 if p.is_file() and not str(p).startswith(str(PROF) + os.sep) and not str(p).startswith(str(W)))
s.check("profiles are written only under COORDN8_HOME/profiles (no other new file in the sandbox homes)",
        outside == [] and sorted(x.name for x in PROF.iterdir()) == ["acme.json"], outside)
rls = run(BRAND, ["list"], env=ENV)
s.check("list shows the saved profile", rls.returncode == 0 and any(x.get("slug") == "acme" and x.get("valid")
                                                                    for x in jline(rls).get("profiles", [])), rls.stdout[-200:])
# diff + apply
build = W / "build"
build.mkdir()
dsp = build / "design_system.json"
dsp.write_text(json.dumps({"schema": 1, "kit": {"id": "ops-console"}, "palette": {"family": "harbor",
               "roles": {"paper": "#f6f7f9", "accent": "#225fa0", "ink": "#151a21"}}}, indent=2), encoding="utf-8")
orig = dsp.read_bytes()
ra = run(BRAND, ["apply", "acme", "--build", build], env=ENV)
ja = jline(ra)
s.check("apply is refused without --apply: exit 1, 'refused', design system byte-identical",
        ra.returncode == 1 and ja.get("refused") and ja.get("applied") is None and dsp.read_bytes() == orig, ra.stdout[-300:])
ch = {(c["skin"], c["role"]): c for c in (ja.get("diff") or {}).get("changes", [])}
s.check("the diff against the current design system is shown (accent #225fa0 -> #0a66c2 with ΔE)",
        ("light", "accent") in ch and ch[("light", "accent")]["current"] == "#225fa0"
        and ch[("light", "accent")]["proposed"] == "#0a66c2" and ch[("light", "accent")]["delta_e"]
        and "+ light.accent" in ja["diff"]["diff_text"], list(ch)[:5])
ri2 = run(BRAND, ["colours", "accent=#0a66c2", "--build", build], env=ENV)
s.check("an intake with --build but no --apply also writes nothing (diff only)",
        ri2.returncode == 1 and jline(ri2).get("refused") and dsp.read_bytes() == orig, ri2.stdout[-200:])
rA = run(BRAND, ["apply", "acme", "--build", build, "--apply"], env=ENV)
dsn = json.loads(dsp.read_text()) if dsp.exists() else {}
s.check("with --apply the profile palette is written and every other key is kept",
        rA.returncode == 0 and dsn.get("palette", {}).get("family") == "invented:brand-acme"
        and dsn.get("kit") == {"id": "ops-console"} and dsn.get("brand", {}).get("slug") == "acme"
        and dsn.get("type", {}).get("display") == "Fixture Display, sans-serif", rA.stdout[-300:])
s.check("the applied design system passes the palette gate", run(GATE, [dsp]).returncode == 0, "gate failed")

# URL intake — a local server only, never the internet
hits = []


class H(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        hits.append(self.path)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(FIX)))
th = threading.Thread(target=srv.serve_forever, daemon=True)
th.start()
url = f"http://127.0.0.1:{srv.server_address[1]}/brand_page.html"
ru = run(BRAND, ["url", url, "--slug", "fixture-page", "--save"], env=ENV)
srv.shutdown()
srv.server_close()
ju = jline(ru)
det = {x["role"]: x["value"] for x in ju.get("detected", [])}
s.check("URL intake maps the page's CSS variables to roles (paper, ink, muted, accent, link)",
        ru.returncode == 0 and det.get("paper") == "#fbfaf7" and det.get("ink") == "#1b1a22"
        and det.get("accent") == "#5b2a86" and det.get("muted") == "#6a6775" and det.get("link") == "#2f5fb3",
        det or ru.stdout[-300:])
s.check("URL intake fetches exactly ONE page: the linked stylesheet is not requested (and says so)",
        hits == ["/brand_page.html"] and any("NOT fetched" in n for n in ju.get("notes", [])), hits)
s.check("URL intake ignores the dark-scheme block for light roles and reads fonts (heading, body, mono)",
        det.get("accent") != "#ffd400" and ju.get("fonts", {}).get("mono") == '"Fixture Mono", monospace'
        and ju["fonts"].get("display", "").startswith('"Fixture Display"'), ju.get("fonts"))
s.check("URL profile palette passes the palette gate", ju.get("checks", {}).get("palette_gate") == "PASS", ju.get("checks"))
rdown = run(BRAND, ["url", f"http://127.0.0.1:{srv.server_address[1]}/brand_page.html"], env=ENV)
s.check("unreachable/offline URL -> SKIPPED (exit 2) with the reason, nothing saved",
        rdown.returncode == 2 and jline(rdown).get("status") == "SKIPPED" and "could not fetch" in jline(rdown).get("summary", ""),
        rdown.stdout[-200:])
rfile = run(BRAND, ["url", "file:///etc/hosts"], env=ENV)
s.check("non-http(s) URLs are refused (no file:// reads through the URL path)",
        rfile.returncode == 3 and "http" in jline(rfile).get("summary", ""), rfile.stdout[-200:])
# folder intake
rfo = run(BRAND, ["folder", FIX / "brand_folder"], env=ENV)
jf = jline(rfo)
fd = {x["role"]: x["value"] for x in jf.get("detected", [])}
s.check("folder intake reads token JSON, SCSS and Markdown into roles",
        rfo.returncode == 0 and fd.get("accent") == "#d9480f" and fd.get("paper") == "#fffaf3"
        and fd.get("warning") == "#b35c00" and fd.get("link") == "#1c64b4" and fd.get("positive") == "#2b8a3e",
        fd or rfo.stdout[-300:])
fa = [a for a in jf.get("adjustments", []) if a.get("role") == "accent" and a.get("skin") == "light"]
s.check("folder brand accent below 4.5:1 is adjusted and reported; palette passes the gate",
        fa and fa[0]["from"] == "#d9480f" and fa[0]["to_ratio"] >= 4.5 and jf["checks"]["palette_gate"] == "PASS", fa)

TMP.cleanup()
s.check("suite leaves nothing on disk (temp dir removed)", not T.exists(), str(T))
sys.exit(s.finish())
