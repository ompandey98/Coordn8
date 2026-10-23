#!/usr/bin/env python3
"""
Coordn8 — LOCAL COVER / THUMBNAIL (E9, coverage video-18).

Renders the video's cover images ON THIS MACHINE — no hosted image generator, nothing sent
anywhere. A cover is a real frame of the video plus a title overlay in the active kit:

  1. the scene is rendered at a chosen, SETTLED moment `t` (entrances finished — pick the most
     telling frame of the video) in headless Chromium (Playwright, else the Chrome CLI);
  2. the title (display text; caption marks such as ⟦key:caching⟧ colour a word with its kit
     role) is set over a scrim in the kit's display face;
  3. one PNG per size — by default 1280x720 (landscape thumbnail) and 1080x1920 (portrait).

Because the cover is rendered from the same scene and the same design system, its words are
exact (no garbled generated lettering) and its look matches the video.

Measured, not assumed (Playwright path): the title's box is read back from the page and
checked — inside the 5 % safe area, no horizontal overflow, at most 2 lines, type never below
the legibility floor (44 px at 1080p for a title, scaled to the image height; the title steps
down from its preferred size toward the floor to fit and is truncated with an ellipsis only if
it still cannot fit) — and the title colour's WCAG contrast over its scrim is computed from the
kit colours. Each PNG's real pixel size is read from its header.

No browser here -> exit 2 with the reason and the unlock command (never a fake image).

Usage:
    python3 scripts/render/cover.py --scene video/s1.html --t 4.2 \
        --title "How the ⟦key:cache⟧ keeps it fast" --kit Coordn8/_build/design_system.json \
        --out-dir Coordn8 [--name <slug>-cover] [--sizes 1280x720,1080x1920] [--subtitle "..."]
    python3 scripts/render/cover.py --title "…" --out-dir DIR          # title-only cover on the kit paper
Exit: 0 written and every check passed · 1 written with findings · 2 skipped (no browser) · 3 error.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import struct
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
import caption_marks as cm  # noqa: E402
from lib.common import find_chrome, run  # noqa: E402

DEFAULT_SIZES = ((1280, 720), (1080, 1920))
TITLE_FLOOR_1080 = 44            # PLAYBOOK 2.4a: titles >= 44 px at 1080p
SAFE_PCT = 5.0
SCRIM_ALPHA = 0.94
DEFAULT_ROLES = {"paper": "#11151c", "ink": "#f2f4f8", "muted": "#b7c0cc", "accent": "#f4d35e",
                 "positive": "#a3e3a8", "negative": "#ff9e9e"}


# ----------------------------------------------------------------------------- helpers
def png_size(path: Path) -> tuple:
    """(width, height) from the PNG IHDR chunk — the real pixel size, not what we asked for."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def _have_playwright() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _kit_bits(kit_path: str | None, mode: str | None) -> dict:
    kit = skin = None
    if kit_path:
        kit, skin = cm.load_kit(kit_path)
    sk = cm._skins(kit, skin)
    mode = mode or (kit or {}).get("base", "dark")
    roles = dict(DEFAULT_ROLES)
    roles.update(sk.get(mode) or sk.get("light") or {})
    theme = (kit or {}).get("theme") or {}
    fam = theme.get("family") or {}
    weights = theme.get("weights") or {}
    marks = {n: roles.get(m["role"], roles["accent"]) for n, m in cm.mark_table(kit).items()}
    for r in roles:
        marks.setdefault(r, roles[r])           # a role name used directly as a mark
    return {"roles": roles, "mode": mode, "marks": marks, "kit_id": (kit or {}).get("id"),
            "display": fam.get("display") or fam.get("body") or "Inter, system-ui, sans-serif",
            "body": fam.get("body") or "Inter, system-ui, sans-serif",
            "weight": int(weights.get("strong", 700) or 700)}


def title_html(text: str, marks: dict) -> str:
    """Display title -> escaped HTML with marked spans coloured by their role."""
    toks = cm.tokens(text)
    out, cur, buf = [], None, []

    def flush():
        if not buf:
            return
        s = html.escape(" ".join(buf))
        out.append(f'<span class="mk" data-mark="{html.escape(cur)}" style="color:{marks.get(cur, "inherit")}">{s}</span>'
                   if cur else s)

    for w, idx, name in toks:
        key = name if idx is not None else None
        if key != cur:
            flush()
            buf = []
            cur = key
        buf.append(w)
    flush()
    return " ".join(out)


def cover_page(w: int, h: int, frame_uri: str | None, title: str, subtitle: str | None, kb: dict) -> str:
    r = kb["roles"]
    portrait = h > w
    pref = round((h * 0.105) if not portrait else (w * 0.105))
    floor = max(28, round(TITLE_FLOOR_1080 * (min(w, h) / 1080.0)))
    # inside the safe area on BOTH axes (5 % of each dimension, plus a hair of margin)
    px_, py_ = round(w * (SAFE_PCT + 0.6) / 100.0), round(h * (SAFE_PCT + 0.6) / 100.0)
    pad = round(min(w, h) * SAFE_PCT / 100.0)
    paper = r["paper"]
    pr, pg, pb = cm._hex(paper)
    scrim = f"rgba({pr},{pg},{pb},{SCRIM_ALPHA})"
    if frame_uri and not portrait:
        frame = (f'<div id="frame" style="position:absolute;inset:0;background:url({frame_uri}) center/cover '
                 f'no-repeat"></div>')
        block = (f'position:absolute;left:{px_}px;right:{px_}px;bottom:{py_}px;padding:{round(pad * 0.8)}px '
                 f'{round(pad)}px;background:{scrim};border-radius:{round(pad * 0.4)}px;')
    elif frame_uri:
        fh = round(w * 9 / 16)
        # positioned by the script below: title block + frame + accent rule centred as one group
        frame = (f'<div id="frame" data-portrait="1" style="position:absolute;left:0;right:0;top:{round(h * 0.46)}px;'
                 f'height:{fh}px;background:url({frame_uri}) center/contain no-repeat"></div>'
                 f'<div id="rule" style="position:absolute;left:{px_}px;right:{px_}px;top:{round(h * 0.46) + fh + pad}px;'
                 f'height:{max(4, round(h * 0.004))}px;background:{r["accent"]};opacity:.85"></div>')
        block = (f'position:absolute;left:{px_}px;right:{px_}px;top:{max(py_, round(h * 0.09))}px;padding:{round(pad * 0.6)}px '
                 f'{round(pad * 0.8)}px;background:{scrim};border-radius:{round(pad * 0.4)}px;')
    else:
        frame = ""
        block = (f'position:absolute;left:{px_}px;right:{px_}px;top:50%;transform:translateY(-50%);'
                 f'padding:{round(pad)}px;background:{scrim};border-radius:{round(pad * 0.4)}px;')
    sub = (f'<div id="sub" style="margin-top:{round(pref * 0.28)}px;font:500 {max(floor * 0.62, 28 * min(w, h) / 1080):.0f}px '
           f'{kb["body"]};color:{r["muted"]};line-height:1.25">{html.escape(subtitle)}</div>') if subtitle else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:{paper}}}
#title{{font-family:{kb['display']};font-weight:{kb['weight']};color:{r['ink']};line-height:1.12;
  letter-spacing:-0.01em;overflow-wrap:break-word;word-break:normal}}
#title.clamp{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
</style></head><body>
{frame}
<div id="block" style="{block}"><div id="title" data-pref="{pref}" data-floor="{floor}"
 style="font-size:{pref}px">{title_html(title, kb['marks'])}</div>{sub}</div>
<script>
(function(){{
  var t=document.getElementById('title');
  var pref=+t.dataset.pref, floor=+t.dataset.floor, size=pref;
  function lines(){{ var lh=parseFloat(getComputedStyle(t).lineHeight)||size*1.12; return Math.round(t.scrollHeight/lh); }}
  // step DOWN toward the floor only — never below it
  while(size>floor && lines()>2){{ size-=1; t.style.fontSize=size+'px'; }}
  var truncated=false;
  if(lines()>2){{ t.classList.add('clamp'); truncated=true; }}
  var fr=document.getElementById('frame'), rule=document.getElementById('rule'), bl=document.getElementById('block');
  if(fr && fr.dataset.portrait){{
    var gap={pad}, bh=bl.getBoundingClientRect().height, fh=fr.getBoundingClientRect().height;
    var total=bh+gap+fh+gap+(rule?rule.getBoundingClientRect().height:0);
    var top=Math.max({py_}, Math.round(({h}-total)/2));
    bl.style.top=top+'px'; fr.style.top=(top+bh+gap)+'px';
    if(rule) rule.style.top=(top+bh+gap+fh+gap)+'px';
  }}
  window.__C8_COVER__={{font_px:size, pref:pref, floor:floor, truncated:truncated}};
  document.title='READY';
}})();
</script></body></html>"""


MEASURE_JS = """() => {
  const t = document.getElementById('title'), b = document.getElementById('block');
  const r = b.getBoundingClientRect(), tr = t.getBoundingClientRect();
  const lh = parseFloat(getComputedStyle(t).lineHeight) || 1;
  return Object.assign({}, window.__C8_COVER__ || {}, {
    block: {x: r.x, y: r.y, w: r.width, h: r.height},
    title: {x: tr.x, y: tr.y, w: tr.width, h: tr.height},
    overflow_x: t.scrollWidth > t.clientWidth + 1,
    lines: Math.round(Math.min(t.scrollHeight, tr.height) / lh),
    font_px_computed: parseFloat(getComputedStyle(t).fontSize),
    marks: Array.from(t.querySelectorAll('.mk')).map(s => ({mark: s.dataset.mark, color: getComputedStyle(s).color})),
    vw: innerWidth, vh: innerHeight });
}"""


def _checks(m: dict, w: int, h: int, kb: dict) -> tuple:
    findings, checks = [], {}
    sx, sy = w * SAFE_PCT / 100.0, h * SAFE_PCT / 100.0
    b = m["block"]
    inside = b["x"] >= sx - 1 and b["y"] >= sy - 1 and b["x"] + b["w"] <= w - sx + 1 and b["y"] + b["h"] <= h - sy + 1
    checks["title_inside_safe_area"] = inside
    if not inside:
        findings.append(f"{w}x{h}: title block {round(b['x'])},{round(b['y'])} {round(b['w'])}x{round(b['h'])} "
                        f"leaves the {SAFE_PCT:g}% safe area")
    checks["title_no_horizontal_overflow"] = not m["overflow_x"]
    if m["overflow_x"]:
        findings.append(f"{w}x{h}: title overflows its block horizontally")
    checks["title_at_most_2_lines"] = m["lines"] <= 2
    checks["title_font_px"] = m.get("font_px_computed")
    checks["title_floor_px"] = m.get("floor")
    if m.get("font_px_computed", 0) + 0.5 < m.get("floor", 0):
        findings.append(f"{w}x{h}: title {m.get('font_px_computed')}px is below its {m.get('floor')}px floor")
    if m.get("truncated"):
        findings.append(f"{w}x{h}: title still needs more than 2 lines at the {m.get('floor')}px floor — truncated "
                        f"with an ellipsis; shorten the title")
    r = kb["roles"]
    bg = cm.mix(r["paper"], r["ink"], SCRIM_ALPHA)          # worst case: scrim over solid ink content
    c = cm.contrast(r["ink"], bg)
    checks["title_contrast_worst_case"] = round(c, 2)
    if c < 4.5:
        findings.append(f"{w}x{h}: title contrast {c:.2f}:1 over its scrim (< 4.5:1)")
    for mk in m.get("marks") or []:
        col = kb["marks"].get(mk["mark"])
        if col:
            mc = cm.contrast(col, bg)
            if mc < 3.0:
                findings.append(f"{w}x{h}: marked title word ({mk['mark']}) {col} is {mc:.2f}:1 over the scrim (< 3:1)")
    return checks, findings


# ----------------------------------------------------------------------------- engines
def _render_playwright(scene, t, sizes, title, subtitle, kb, out_dir: Path, name: str) -> dict:
    from playwright.sync_api import sync_playwright
    files, measured, findings = [], {}, []
    with tempfile.TemporaryDirectory(prefix="c8cover-") as td, sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--force-color-profile=srgb", "--allow-file-access-from-files",
                                           "--hide-scrollbars", "--disable-gpu"])
        blocked: list = []

        def guard(route):
            # Nothing leaves this machine: only file: and data: URLs load; everything else is refused
            # (and counted, so the result can prove it).
            u = route.request.url
            if u.startswith(("file:", "data:", "about:")):
                route.continue_()
            else:
                blocked.append(u[:120])
                route.abort()

        try:
            uri = None
            if scene:
                page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
                page.route("**/*", guard)
                page.goto(Path(scene).resolve().as_uri() + f"?t={t:.4f}")
                try:
                    page.wait_for_function("() => document.title === 'READY' || window.__ready === true",
                                           timeout=15000)
                except Exception:  # noqa: BLE001 — a scene may simply not signal
                    page.wait_for_timeout(200)
                page.evaluate("(t) => { if (typeof window.renderAt === 'function') window.renderAt(t); "
                              "else if (typeof window.renderFrame === 'function') window.renderFrame(t); }", t)
                fp = Path(td) / "frame.png"
                page.screenshot(path=str(fp))
                page.close()
                uri = "data:image/png;base64," + base64.b64encode(fp.read_bytes()).decode("ascii")
            for (w, h) in sizes:
                page = browser.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
                page.route("**/*", guard)
                page.set_content(cover_page(w, h, uri, title, subtitle, kb), wait_until="load")
                page.wait_for_function("() => document.title === 'READY'", timeout=10000)
                m = page.evaluate(MEASURE_JS)
                dest = out_dir / f"{name}-{w}x{h}.png"
                page.screenshot(path=str(dest))
                page.close()
                pw_, ph_ = png_size(dest)
                checks, f = _checks(m, w, h, kb)
                checks["png_size_matches"] = (pw_, ph_) == (w, h)
                if (pw_, ph_) != (w, h):
                    f.append(f"{dest.name} is {pw_}x{ph_}, expected {w}x{h}")
                findings += f
                measured[f"{w}x{h}"] = {"measure": m, "checks": checks}
                files.append({"path": str(dest), "width": pw_, "height": ph_})
        finally:
            browser.close()
    return {"engine": "playwright", "files": files, "measured": measured, "findings": findings,
            "network_blocked": blocked}


def _render_chrome_cli(chrome, scene, t, sizes, title, subtitle, kb, out_dir: Path, name: str) -> dict:
    files, findings = [], []
    with tempfile.TemporaryDirectory(prefix="c8cover-") as td:
        uri = None
        if scene:
            fp = Path(td) / "frame.png"
            cp = run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                      "--virtual-time-budget=1500", "--window-size=1920,1080", f"--screenshot={fp}",
                      Path(scene).resolve().as_uri() + f"?t={t:.4f}"], timeout=120)
            if cp.returncode != 0 or not fp.exists():
                raise RuntimeError(f"frame capture failed: {cp.stderr[:200]}")
            uri = "data:image/png;base64," + base64.b64encode(fp.read_bytes()).decode("ascii")
        for (w, h) in sizes:
            hp = Path(td) / f"cover-{w}x{h}.html"
            hp.write_text(cover_page(w, h, uri, title, subtitle, kb), encoding="utf-8")
            dest = out_dir / f"{name}-{w}x{h}.png"
            cp = run([chrome, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                      "--virtual-time-budget=1500", f"--window-size={w},{h}", f"--screenshot={dest}",
                      hp.resolve().as_uri()], timeout=120)
            if cp.returncode != 0 or not dest.exists():
                raise RuntimeError(f"cover capture failed: {cp.stderr[:200]}")
            pw_, ph_ = png_size(dest)
            if (pw_, ph_) != (w, h):
                findings.append(f"{dest.name} is {pw_}x{ph_}, expected {w}x{h}")
            files.append({"path": str(dest), "width": pw_, "height": ph_})
    findings.append("title fit NOT measured (Chrome CLI path has no DOM read-back); install Playwright for "
                    "the measured checks")
    return {"engine": "chrome-cli", "files": files, "measured": {}, "findings": findings}


def render_cover(scene=None, t=3.0, title="", subtitle=None, kit=None, mode=None, out_dir=".",
                 name="cover", sizes=DEFAULT_SIZES, engine="auto") -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if scene and not Path(scene).exists():
        return {"status": "ERROR", "reason": f"scene {scene} does not exist", "files": []}
    if not str(title).strip():
        return {"status": "ERROR", "reason": "a cover needs a title", "files": []}
    kb = _kit_bits(kit, mode)
    base = {"scene": str(scene) if scene else None, "t": t, "title": cm.strip(title), "kit": kb["kit_id"],
            "mode": kb["mode"], "hosted_services": "none — rendered locally"}
    use_pw = engine in ("auto", "playwright") and _have_playwright()
    chrome = None if use_pw else (find_chrome() if engine in ("auto", "chrome") else None)
    if not use_pw and not chrome:
        return dict(base, status="SKIPPED", files=[],
                    reason="no headless browser here (neither Playwright nor Chrome/Chromium found)",
                    unlock="pip install playwright && python3 -m playwright install chromium")
    try:
        r = (_render_playwright(scene, t, sizes, title, subtitle, kb, out, name) if use_pw
             else _render_chrome_cli(chrome, scene, t, sizes, title, subtitle, kb, out, name))
    except Exception as ex:  # noqa: BLE001 — a browser failure is an honest skip, never a fake image
        msg = f"{type(ex).__name__}: {str(ex)[:300]}"
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            return dict(base, status="SKIPPED", files=[], reason=f"browser not installed for Playwright: {msg}",
                        unlock="python3 -m playwright install chromium")
        return dict(base, status="ERROR", files=[], reason=msg)
    return dict(base, **r, status="REVIEW" if r["findings"] else "PASS")


def _sizes(s: str) -> tuple:
    out = []
    for part in s.split(","):
        w, h = part.lower().split("x")
        out.append((int(w), int(h)))
    return tuple(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a local cover/thumbnail from a scene frame + title.")
    ap.add_argument("--scene", help="scene HTML (a pure function of t: scene.html?t=…); omit for a title-only cover")
    ap.add_argument("--t", type=float, default=3.0, help="the settled moment to use (seconds)")
    ap.add_argument("--title", required=True, help="display title; caption marks colour words (⟦key:…⟧)")
    ap.add_argument("--subtitle")
    ap.add_argument("--kit", help="kit JSON or design_system.json (the active design system)")
    ap.add_argument("--mode", choices=["light", "dark"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", default="cover")
    ap.add_argument("--sizes", default="1280x720,1080x1920")
    ap.add_argument("--engine", choices=["auto", "playwright", "chrome"], default="auto")
    a = ap.parse_args(argv)
    try:
        sizes = _sizes(a.sizes)
    except ValueError:
        print(json.dumps({"status": "ERROR", "reason": f"bad --sizes {a.sizes!r} (want WxH,WxH)"}))
        return 3
    r = render_cover(a.scene, a.t, a.title, a.subtitle, a.kit, a.mode, a.out_dir, a.name, sizes, a.engine)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.stderr.write(f"[coordn8:cover] {r['status']}: "
                     f"{', '.join(Path(f['path']).name for f in r.get('files', [])) or r.get('reason', '')}\n")
    return {"PASS": 0, "REVIEW": 1, "SKIPPED": 2}.get(r["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
