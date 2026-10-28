#!/usr/bin/env python3
"""
Coordn8 — OPTIONAL SKILL: render a provided HTML mockup/demo into REAL
screenshots, populated with hypothetical/spoofed data, and annotate them from
plain directions ("zoom in on this part", "circle this part").

THIS IS NOT PART OF THE NORMAL PIPELINE. It never runs automatically, no gate
depends on it, and `run_gates.py` does not call it. It exists so that WHEN a user
provides a local HTML file / simulation and asks for real screenshots — often of
a view they cannot reach live (logged-in dashboard, paid page, SSO-gated screen)
— Coordn8 already has the capability ready instead of improvising. Invoke it only
on a real ask (PLAYBOOK: "OPTIONAL SKILL — RENDER A PROVIDED HTML MOCKUP/DEMO").

Two subcommands:

  shot     — render a LOCAL html file headlessly and capture a genuine PNG. Inject
             hypothetical data so an un-authable page still populates; optionally
             stub/block external network so a page that would hang on an auth
             redirect or a dead API renders its shell against the placeholder data.
             Prefers Playwright (real init-script injection + network routing);
             falls back to a headless-Chrome CLI screenshot (data injected by
             rewriting a temp copy of the HTML). Never fabricates a screenshot it
             didn't actually take — if no browser is available it SKIPS honestly.

  annotate — composite a crisp vector overlay over an existing screenshot from
             plain directions: circle / box / arrow / label / spotlight (dim all
             but a region) / zoom (magnified loupe inset of a region). The overlay
             is built as deterministic SVG (testable with zero deps) and, when a
             browser is available, rasterized to a final PNG.

Honesty: the screenshot is a REAL render of the REAL markup/CSS/JS (only the DATA
is synthetic). The synthetic data still obeys the content bans (PLAYBOOK 1.5): no
real IDs, customer data, secrets, or internal URLs, even in "fake" data.

Usage:
  python3 scripts/render/mockup_shot.py shot --html demo/dashboard.html \
      --mock-json fake_data.json --offline --viewport 1600,1000 --out shots/dash.png
  python3 scripts/render/mockup_shot.py annotate --image shots/dash.png \
      --circle 1180,240,220,90 --zoom 900,300,600,400 --label 40,40,"Totals" \
      --out shots/dash_annotated.png
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import CHROME_QUIET, find_chrome, run  # noqa: E402

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_SKIPPED = 2  # honest degrade: no browser here (mirrors gate SKIPPED)

# Overlay styling — a crisp, high-visibility marker language. Stroke width and
# font scale with the image so annotations read at any resolution.
ACCENT = "#ff2d55"       # circle/box/arrow — high-contrast marker
LABEL_BG = "#111827"
LABEL_FG = "#ffffff"
DIM = "rgba(0,0,0,0.62)"  # spotlight scrim over everything but the focus region


# ---- pure helpers (zero-dep, unit-tested) ---------------------------------


def _valid_png(p: Path, min_bytes: int = 512) -> bool:
    """Is `p` a real, non-trivial PNG? Used to judge the ARTIFACT rather than the
    exit code. Some headless-Chrome builds write a perfectly good screenshot and
    THEN hang or exit non-zero (e.g. the 'allocator multiple times' quirk). Judging
    only the exit code then reports failure for a file that exists and is fine —
    while judging only existence would risk claiming a truncated/blank file. So we
    verify the bytes: PNG magic, an IHDR, and a plausible size."""
    try:
        if not p.exists() or p.stat().st_size < min_bytes:
            return False
        head = p.open("rb").read(32)
        return head[:8] == b"\x89PNG\r\n\x1a\n" and b"IHDR" in head
    except OSError:
        return False



def png_size(path: Path) -> tuple[int, int] | None:
    """Read (width, height) from a PNG header with no third-party deps.

    Lets `annotate` size its overlay to the real screenshot without Pillow. The
    IHDR chunk carries width/height as two big-endian uint32 at bytes 16..24.
    Returns None if the file isn't a PNG we can read.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    w, h = struct.unpack(">II", head[16:24])
    return (int(w), int(h))


def _nums(spec: str, n: int, name: str) -> list[float]:
    parts = [p.strip() for p in str(spec).split(",") if p.strip() != ""]
    if len(parts) != n:
        raise ValueError(f"--{name} wants {n} comma-separated numbers, got {spec!r}")
    try:
        return [float(p) for p in parts]
    except ValueError as e:
        raise ValueError(f"--{name} has a non-number: {spec!r}") from e


def parse_annotations(args: argparse.Namespace) -> list[dict]:
    """Turn the CLI direction flags into an ordered list of annotation dicts.

    Order is preserved so overlays stack predictably (a spotlight drawn first is
    under the circle drawn next). Each direction maps to one plain user ask:
      --circle x,y,w,h      "circle this part"
      --box x,y,w,h         "box / highlight this part"
      --arrow x1,y1,x2,y2   "point an arrow at this"
      --label x,y,text      "put a caption here"
      --spotlight x,y,w,h   "focus on this / dim the rest"
      --zoom x,y,w,h        "zoom in on this part" (magnified loupe inset)
    """
    out: list[dict] = []
    for c in (args.circle or []):
        x, y, w, h = _nums(c, 4, "circle")
        out.append({"type": "circle", "x": x, "y": y, "w": w, "h": h})
    for b in (args.box or []):
        x, y, w, h = _nums(b, 4, "box")
        out.append({"type": "box", "x": x, "y": y, "w": w, "h": h})
    for a in (args.arrow or []):
        x1, y1, x2, y2 = _nums(a, 4, "arrow")
        out.append({"type": "arrow", "x1": x1, "y1": y1, "x2": x2, "y2": y2})
    for s in (args.spotlight or []):
        x, y, w, h = _nums(s, 4, "spotlight")
        out.append({"type": "spotlight", "x": x, "y": y, "w": w, "h": h})
    for z in (args.zoom or []):
        x, y, w, h = _nums(z, 4, "zoom")
        out.append({"type": "zoom", "x": x, "y": y, "w": w, "h": h})
    # labels are "x,y,text" — text may itself contain no comma-splitting issue
    # because we split only twice.
    for lb in (args.label or []):
        parts = str(lb).split(",", 2)
        if len(parts) != 3:
            raise ValueError(f"--label wants x,y,text — got {lb!r}")
        x = float(parts[0].strip())
        y = float(parts[1].strip())
        out.append({"type": "label", "x": x, "y": y, "text": parts[2].strip()})
    return out


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _inset_rect(zx, zy, zw, zh, W, H, iw, ih):
    """Pick a corner for a zoom loupe that does NOT overlap the source region.
    Prefer the corner farthest from the region's center; clamp inside the frame.
    """
    cx, cy = zx + zw / 2, zy + zh / 2
    right = cx < W / 2          # region on the left -> inset on the right
    bottom = cy < H / 2         # region on the top  -> inset on the bottom
    margin = max(12.0, min(W, H) * 0.02)
    ix = (W - iw - margin) if right else margin
    iy = (H - ih - margin) if bottom else margin
    return ix, iy


def build_overlay_svg(bg_href: str, width: int, height: int,
                      annotations: list[dict]) -> str:
    """Build a self-contained SVG: the screenshot as the background <image>, plus
    the vector annotation overlay on top. A browser rasterizes this to the final
    PNG; the string itself is deterministic so it can be unit-tested with no deps.
    """
    scale = max(1.0, min(width, height) / 900.0)
    sw = round(3.0 * scale, 2)          # marker stroke width
    font = round(20.0 * scale, 1)       # label font size
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">')
    parts.append(
        '<defs>'
        f'<marker id="c8arrow" markerWidth="12" markerHeight="12" refX="8" '
        f'refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{ACCENT}"/>'
        '</marker>'
        '<filter id="c8shadow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feDropShadow dx="0" dy="1" stdDeviation="1.5" '
        'flood-opacity="0.5"/></filter>'
        '</defs>')
    if bg_href:
        parts.append(
            f'<image href="{_esc(bg_href)}" x="0" y="0" '
            f'width="{width}" height="{height}" '
            f'preserveAspectRatio="xMidYMid slice"/>')

    # spotlight scrim(s): dim the whole frame except each focus rect (drawn as a
    # mask). Multiple spotlights punch multiple holes in one scrim.
    spots = [a for a in annotations if a["type"] == "spotlight"]
    if spots:
        holes = "".join(
            f'<rect x="{a["x"]}" y="{a["y"]}" width="{a["w"]}" '
            f'height="{a["h"]}" rx="{round(8*scale,1)}" fill="black"/>'
            for a in spots)
        parts.append(
            f'<mask id="c8spot"><rect x="0" y="0" width="{width}" '
            f'height="{height}" fill="white"/>{holes}</mask>'
            f'<rect x="0" y="0" width="{width}" height="{height}" '
            f'fill="{DIM}" mask="url(#c8spot)"/>')

    for a in annotations:
        t = a["type"]
        if t == "circle":
            cx = a["x"] + a["w"] / 2
            cy = a["y"] + a["h"] / 2
            rx = a["w"] / 2
            ry = a["h"] / 2
            parts.append(
                f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" '
                f'fill="none" stroke="{ACCENT}" stroke-width="{sw}" '
                f'filter="url(#c8shadow)"/>')
        elif t == "box":
            parts.append(
                f'<rect x="{a["x"]}" y="{a["y"]}" width="{a["w"]}" '
                f'height="{a["h"]}" rx="{round(6*scale,1)}" fill="none" '
                f'stroke="{ACCENT}" stroke-width="{sw}" filter="url(#c8shadow)"/>')
        elif t == "arrow":
            parts.append(
                f'<line x1="{a["x1"]}" y1="{a["y1"]}" x2="{a["x2"]}" '
                f'y2="{a["y2"]}" stroke="{ACCENT}" stroke-width="{sw}" '
                f'marker-end="url(#c8arrow)" filter="url(#c8shadow)"/>')
        elif t == "label":
            text = a["text"]
            pad = round(8 * scale, 1)
            box_w = round(len(text) * font * 0.62 + pad * 2, 1)
            box_h = round(font + pad * 2, 1)
            parts.append(
                f'<g filter="url(#c8shadow)">'
                f'<rect x="{a["x"]}" y="{a["y"]}" width="{box_w}" '
                f'height="{box_h}" rx="{round(6*scale,1)}" fill="{LABEL_BG}"/>'
                f'<text x="{a["x"]+pad}" y="{a["y"]+pad+font*0.82}" '
                f'font-family="Inter,Segoe UI,Arial,sans-serif" '
                f'font-size="{font}" fill="{LABEL_FG}">{_esc(text)}</text></g>')
        elif t == "zoom":
            zx, zy, zw, zh = a["x"], a["y"], a["w"], a["h"]
            # loupe inset ~1/3 of the frame, preserving the region's aspect.
            iw = min(width * 0.38, max(220.0, zw))
            ih = iw * (zh / zw) if zw else iw
            if ih > height * 0.5:
                ih = height * 0.5
                iw = ih * (zw / zh) if zh else iw
            ix, iy = _inset_rect(zx, zy, zw, zh, width, height, iw, ih)
            s = min(iw / zw, ih / zh) if (zw and zh) else 1.0
            clip = f"c8zoomclip{round(zx)}_{round(zy)}"
            # source-region outline + magnified crop drawn into the inset.
            parts.append(
                f'<rect x="{zx}" y="{zy}" width="{zw}" height="{zh}" '
                f'fill="none" stroke="{ACCENT}" stroke-width="{sw}" '
                f'stroke-dasharray="{round(6*scale,1)} {round(4*scale,1)}"/>')
            parts.append(
                f'<clipPath id="{clip}"><rect x="{ix}" y="{iy}" width="{iw}" '
                f'height="{ih}" rx="{round(8*scale,1)}"/></clipPath>')
            if bg_href:
                tx = ix - zx * s
                ty = iy - zy * s
                parts.append(
                    f'<g clip-path="url(#{clip})">'
                    f'<image href="{_esc(bg_href)}" x="0" y="0" '
                    f'width="{width}" height="{height}" '
                    f'transform="translate({round(tx,2)},{round(ty,2)}) '
                    f'scale({round(s,4)})"/></g>')
            parts.append(
                f'<rect x="{ix}" y="{iy}" width="{iw}" height="{ih}" '
                f'rx="{round(8*scale,1)}" fill="none" stroke="{ACCENT}" '
                f'stroke-width="{sw}" filter="url(#c8shadow)"/>')
    parts.append("</svg>")
    return "".join(parts)


def inject_mock_html(html_text: str, mock: dict | None, fill_js: str | None,
                     stub_network: bool) -> str:
    """Rewrite a copy of the HTML so a page needing data/auth still renders.

    We prepend a <script> as early as possible (right after <head>, or at the top)
    so it runs BEFORE the page's own scripts. It: (1) publishes the synthetic data
    on `window.__COORDN8_MOCK__`; (2) optionally stubs `fetch`/XHR to return that
    data and never hit the network (the CLI-Chrome substitute for Playwright's
    real request routing — a page that would hang on an auth redirect or dead API
    instead resolves against placeholder data); (3) runs an optional post-load
    `fill_js` to populate fields. This is best-effort for the Chrome-CLI path;
    the Playwright path does the same with real init scripts + route blocking.
    """
    mock_json = json.dumps(mock or {})
    fill = fill_js or ""
    stub = ""
    if stub_network:
        stub = (
            "var __m=window.__COORDN8_MOCK__||{};"
            "var __resp=function(){return Promise.resolve({ok:true,status:200,"
            "json:function(){return Promise.resolve(__m);},"
            "text:function(){return Promise.resolve(JSON.stringify(__m));}});};"
            "window.fetch=function(){return __resp();};"
            "try{var _o=XMLHttpRequest.prototype.open;"
            "XMLHttpRequest.prototype.send=function(){"
            "try{this.readyState=4;this.status=200;"
            "this.responseText=JSON.stringify(__m);"
            "if(this.onreadystatechange)this.onreadystatechange();"
            "if(this.onload)this.onload();}catch(e){}};}catch(e){}")
    script = (
        "<script>/* coordn8 mockup injection — synthetic data only */"
        f"window.__COORDN8_MOCK__={mock_json};{stub}"
        "document.addEventListener('DOMContentLoaded',function(){try{"
        f"{fill}"
        "}catch(e){console.error('coordn8 fill error',e);}});</script>")
    lower = html_text.lower()
    i = lower.find("<head>")
    if i != -1:
        cut = i + len("<head>")
        return html_text[:cut] + script + html_text[cut:]
    i = lower.find("<html")
    if i != -1:
        gt = html_text.find(">", i)
        if gt != -1:
            return html_text[:gt + 1] + script + html_text[gt + 1:]
    return script + html_text


# ---- browser-backed capture (honest degrade when no browser) --------------

def _shot_playwright(url: str, out: Path, vw: int, vh: int, mock: dict | None,
                     fill_js: str | None, offline: bool,
                     full_page: bool) -> int | None:
    """Return an exit code, or None if Playwright isn't importable (caller
    falls back to the Chrome CLI). Kept import-local so this module loads with
    no third-party deps present."""
    try:
        from playwright.sync_api import sync_playwright  # noqa
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": vw, "height": vh})
            if offline:
                # allow the local file + data URIs; abort anything external so an
                # auth redirect / dead API can't hang the render.
                def _route(route):
                    u = route.request.url
                    if u.startswith("file:") or u.startswith("data:"):
                        return route.continue_()
                    return route.abort()
                ctx.route("**/*", _route)
            page = ctx.new_page()
            if mock is not None:
                page.add_init_script(
                    f"window.__COORDN8_MOCK__ = {json.dumps(mock)};")
            page.goto(url, wait_until="load", timeout=45000)
            if fill_js:
                page.evaluate(fill_js)
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=full_page)
            browser.close()
        sys.stderr.write(f"[mockup_shot] playwright captured {out}\n")
        return EXIT_OK if out.exists() else EXIT_FAIL
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[mockup_shot] playwright render failed: {e}\n")
        return EXIT_FAIL


def _shot_chrome_cli(html: Path, out: Path, vw: int, vh: int, mock: dict | None,
                     fill_js: str | None, offline: bool) -> int:
    chrome = find_chrome()
    if not chrome:
        sys.stderr.write(
            "[mockup_shot] no browser here — cannot capture a screenshot. This "
            "is an honest skip, not a fake image. Unlock with: pip install "
            "playwright && python3 -m playwright install chromium (sudo-free), "
            "or install Chrome/Chromium.\n")
        return EXIT_SKIPPED
    render_file = html
    tmp = None
    if mock is not None or fill_js or offline:
        # rewrite a temp copy with the injection so the page populates without a
        # live backend. (Playwright does this more faithfully; this is the CLI
        # substitute.) offline via fetch/XHR stub, not a true network block.
        html_text = html.read_text(encoding="utf-8", errors="replace")
        injected = inject_mock_html(html_text, mock, fill_js, stub_network=offline)
        tmp = html.with_name(html.stem + ".coordn8-mock.tmp.html")
        tmp.write_text(injected, encoding="utf-8")
        render_file = tmp
    out.parent.mkdir(parents=True, exist_ok=True)
    url = f"{render_file.resolve().as_uri()}"
    cp = run([chrome, *CHROME_QUIET, "--headless=new", "--disable-gpu", "--no-sandbox",
              "--hide-scrollbars", f"--window-size={vw},{vh}",
              "--virtual-time-budget=2000",
              f"--screenshot={out}", url], timeout=90)
    if tmp is not None:
        try:
            tmp.unlink()
        except OSError:
            pass
    if cp.returncode != 0 or not out.exists():
        # Judge the ARTIFACT, not just the exit code. Chrome sometimes writes a
        # perfectly valid screenshot and then hangs or exits non-zero; reporting
        # failure for a good file is as wrong as claiming a file we never made.
        if _valid_png(out):
            sys.stderr.write(
                f"[mockup_shot] chrome exited {cp.returncode} "
                f"({(cp.stderr or '').strip()[:120]}) BUT the screenshot was written and "
                f"validated as a real PNG ({out.stat().st_size} bytes) — treating as "
                f"captured. The image is verified; the exit code is not.\n")
        else:
            sys.stderr.write(f"[mockup_shot] chrome screenshot failed: "
                             f"{cp.stderr[:300]}\n")
            return EXIT_FAIL
    if offline:
        sys.stderr.write("[mockup_shot] note: Chrome-CLI path stubs fetch/XHR "
                         "for --offline; it does not hard-block the network. Use "
                         "Playwright for true request routing.\n")
    sys.stderr.write(f"[mockup_shot] chrome captured {out}\n")
    return EXIT_OK


def shot(html: Path, out: Path, viewport: str, mock_json: Path | None,
         fill_js_path: Path | None, offline: bool, full_page: bool) -> int:
    if not html.exists():
        sys.stderr.write(f"[mockup_shot] no HTML at {html}\n")
        return EXIT_FAIL
    try:
        vw, vh = (int(x) for x in viewport.split(","))
    except ValueError:
        sys.stderr.write(f"[mockup_shot] bad --viewport {viewport!r}; want W,H\n")
        return EXIT_FAIL
    mock = None
    if mock_json is not None:
        if not mock_json.exists():
            sys.stderr.write(f"[mockup_shot] no --mock-json at {mock_json}\n")
            return EXIT_FAIL
        mock = json.loads(mock_json.read_text(encoding="utf-8"))
    fill_js = None
    if fill_js_path is not None:
        if not fill_js_path.exists():
            sys.stderr.write(f"[mockup_shot] no --fill-js at {fill_js_path}\n")
            return EXIT_FAIL
        fill_js = fill_js_path.read_text(encoding="utf-8")

    url = f"{html.resolve().as_uri()}"
    rc = _shot_playwright(url, out, vw, vh, mock, fill_js, offline, full_page)
    if rc is not None:
        return rc
    return _shot_chrome_cli(html, out, vw, vh, mock, fill_js, offline)


def _rasterize_svg(svg_path: Path, out: Path, width: int, height: int) -> int:
    chrome = find_chrome()
    if not chrome:
        sys.stderr.write(
            f"[mockup_shot] overlay SVG written to {svg_path}, but no browser "
            f"here to rasterize it to PNG. Render it later (open the SVG, or "
            f"install a browser), or use it directly as a page figure. Honest "
            f"skip — no fake PNG.\n")
        return EXIT_SKIPPED
    url = f"{svg_path.resolve().as_uri()}"
    cp = run([chrome, *CHROME_QUIET, "--headless=new", "--disable-gpu", "--no-sandbox",
              "--hide-scrollbars", f"--window-size={width},{height}",
              "--default-background-color=00000000",
              f"--screenshot={out}", url], timeout=90)
    if cp.returncode != 0 or not out.exists():
        # Same rule as the capture path: verify the produced PNG rather than
        # trusting the exit code, so a Chrome that renders then hangs is not
        # reported as a failure it wasn't.
        if _valid_png(out):
            sys.stderr.write(
                f"[mockup_shot] chrome exited {cp.returncode} "
                f"({(cp.stderr or '').strip()[:120]}) BUT the raster was written and "
                f"validated as a real PNG ({out.stat().st_size} bytes) — treating as "
                f"rendered.\n")
        else:
            sys.stderr.write(f"[mockup_shot] rasterize failed: {cp.stderr[:300]}\n")
            return EXIT_FAIL
    sys.stderr.write(f"[mockup_shot] annotated screenshot -> {out}\n")
    return EXIT_OK


def annotate(image: Path, out: Path, annotations: list[dict],
             size: str | None) -> int:
    if not image.exists():
        sys.stderr.write(f"[mockup_shot] no --image at {image}\n")
        return EXIT_FAIL
    if size:
        try:
            width, height = (int(x) for x in size.split(","))
        except ValueError:
            sys.stderr.write(f"[mockup_shot] bad --size {size!r}; want W,H\n")
            return EXIT_FAIL
    else:
        wh = png_size(image)
        if wh is None:
            sys.stderr.write(
                f"[mockup_shot] could not read PNG size of {image} — pass "
                f"--size W,H (only PNG headers are read dependency-free).\n")
            return EXIT_FAIL
        width, height = wh

    bg_href = image.resolve().as_uri()
    svg = build_overlay_svg(bg_href, width, height, annotations)
    svg_path = out.with_suffix(".overlay.svg")
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    svg_path.write_text(svg, encoding="utf-8")
    return _rasterize_svg(svg_path, out, width, height)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render + annotate an HTML mockup "
                                             "(OPTIONAL Coordn8 skill).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("shot", help="render a local HTML mockup to a real PNG")
    s.add_argument("--html", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--viewport", default="1600,1000", help="W,H")
    s.add_argument("--mock-json", default=None,
                   help="JSON of synthetic data -> window.__COORDN8_MOCK__")
    s.add_argument("--fill-js", default=None,
                   help="JS file run after load to populate the DOM")
    s.add_argument("--offline", action="store_true",
                   help="stub/block external network so an auth-gated page "
                        "still renders its shell")
    s.add_argument("--full-page", action="store_true",
                   help="capture the full scroll height (Playwright path)")

    a = sub.add_parser("annotate", help="overlay circle/box/arrow/label/"
                                        "spotlight/zoom on a screenshot")
    a.add_argument("--image", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--size", default=None, help="W,H (else read from PNG header)")
    a.add_argument("--circle", action="append", metavar="x,y,w,h")
    a.add_argument("--box", action="append", metavar="x,y,w,h")
    a.add_argument("--arrow", action="append", metavar="x1,y1,x2,y2")
    a.add_argument("--spotlight", action="append", metavar="x,y,w,h")
    a.add_argument("--zoom", action="append", metavar="x,y,w,h")
    a.add_argument("--label", action="append", metavar="x,y,text")

    args = ap.parse_args()
    if args.cmd == "shot":
        return shot(Path(args.html), Path(args.out), args.viewport,
                    Path(args.mock_json) if args.mock_json else None,
                    Path(args.fill_js) if args.fill_js else None,
                    args.offline, args.full_page)
    if args.cmd == "annotate":
        try:
            anns = parse_annotations(args)
        except ValueError as e:
            sys.stderr.write(f"[mockup_shot] {e}\n")
            return EXIT_FAIL
        if not anns:
            sys.stderr.write("[mockup_shot] no annotations given — pass at least "
                             "one of --circle/--box/--arrow/--label/--spotlight/"
                             "--zoom.\n")
            return EXIT_FAIL
        return annotate(Path(args.image), Path(args.out), anns, args.size)
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
