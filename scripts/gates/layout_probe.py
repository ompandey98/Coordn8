#!/usr/bin/env python3
"""
Coordn8 gate — LAYOUT / COMPOSITION (video, also usable on any rendered page).

Enforces the composition discipline of PLAYBOOK.md 2.4a on the ACTUAL geometry
of rendered scenes — the check that was missing while cluttered, overflowing,
misaligned frames shipped. Timing (beat_sync) and container sanity (media_probe)
never look at whether content physically fits the frame; this does.

Two complementary modes, both ADVISORY (findings are leads, never a veto):

1. GEOMETRY CONTRACT (deterministic, the tested core) — validate a layout.json
   describing every visible element's bounding box, sampled at one or more `t`
   values per scene. Flags, per PLAYBOOK 2.4a:
     - overflow / clip     — a box crosses the frame edge
     - safe-area intrusion — a box crosses the 5% margin (crowds the edge)
     - oversize            — a box wider/taller than the frame itself
     - collision           — two text-bearing boxes overlap
     - tiny type           — a text box below the font-size floor
   Data contract (deviate if your project needs to — this is a default):
     {
       "frame": {"width": 1920, "height": 1080},
       "safe_margin_pct": 0.05,
       "min_font_px": {"body": 28, "title": 44},
       "samples": [
         {"scene": "s1", "t": 0.0, "boxes": [
            {"id": "title", "role": "title", "x": 96, "y": 60,
             "w": 900, "h": 80, "font_px": 48, "text": true},
            ...
         ]},
         ...
       ]
     }
   role is one of "title" | "body" (default); a box with "text": true is
   considered for collisions and (if font_px present) the font floor.

2. PIXEL PROBE (generic, optional) — given a rendered frame PNG (`--shot`),
   measure the bounding box of non-background content and flag content that
   reaches the frame edge / safe area (overflow or crowding) or occupies so
   little of the frame that the composition is scattered/barren. Needs Pillow;
   SKIPPED honestly if it is not installed.

Usage:
    python3 scripts/gates/layout_probe.py layout.json
    python3 scripts/gates/layout_probe.py --shot _build/frames/s1/frame_000000.png
    python3 scripts/gates/layout_probe.py layout.json --shot frame.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, load_json)

DEFAULT_FRAME = {"width": 1920, "height": 1080}
DEFAULT_SAFE_PCT = 0.05
DEFAULT_FONT_FLOOR = {"body": 28, "title": 44}


def _overlap(a: dict, b: dict) -> bool:
    """True if two boxes overlap by more than a hairline (anti false-positive)."""
    eps = 1.0
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ox = min(ax2, bx2) - max(a["x"], b["x"])
    oy = min(ay2, by2) - max(a["y"], b["y"])
    return ox > eps and oy > eps


def check_geometry(data: dict) -> list[str]:
    frame = data.get("frame", DEFAULT_FRAME)
    fw = float(frame.get("width", DEFAULT_FRAME["width"]))
    fh = float(frame.get("height", DEFAULT_FRAME["height"]))
    safe_pct = float(data.get("safe_margin_pct", DEFAULT_SAFE_PCT))
    floor = {**DEFAULT_FONT_FLOOR, **(data.get("min_font_px") or {})}
    mx, my = fw * safe_pct, fh * safe_pct

    findings: list[str] = []
    for sample in data.get("samples", []):
        where = f"scene {sample.get('scene', '?')} @ t={sample.get('t', '?')}"
        boxes = sample.get("boxes", [])
        text_boxes = []
        for b in boxes:
            try:
                x, y, w, h = float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"])
            except (KeyError, TypeError, ValueError):
                findings.append(f"{where}: box '{b.get('id', '?')}' has no valid x/y/w/h.")
                continue
            bid = b.get("id", "?")
            x2, y2 = x + w, y + h

            # overflow / clip — crosses the frame edge
            if x < 0 or y < 0 or x2 > fw or y2 > fh:
                findings.append(
                    f"{where}: '{bid}' OVERFLOWS the frame "
                    f"(box {x:.0f},{y:.0f}..{x2:.0f},{y2:.0f} vs frame {fw:.0f}x{fh:.0f}) "
                    f"— it is clipped by the edge.")
            # oversize — bigger than the frame in a dimension
            elif w > fw or h > fh:
                findings.append(
                    f"{where}: '{bid}' is larger than the frame "
                    f"({w:.0f}x{h:.0f} vs {fw:.0f}x{fh:.0f}).")
            # safe-area intrusion — inside the frame but crowding the edge
            elif x < mx or y < my or x2 > fw - mx or y2 > fh - my:
                findings.append(
                    f"{where}: '{bid}' crosses the {safe_pct*100:.0f}% safe area "
                    f"(margins {mx:.0f}x{my:.0f}) — it crowds the frame edge.")

            # font floor for text
            if b.get("text") and b.get("font_px") is not None:
                role = b.get("role", "body")
                need = floor.get(role, floor.get("body", DEFAULT_FONT_FLOOR["body"]))
                try:
                    fp = float(b["font_px"])
                    if fp < need:
                        findings.append(
                            f"{where}: '{bid}' text is {fp:.0f}px, below the "
                            f"{role} floor of {need:.0f}px — too small to read.")
                except (TypeError, ValueError):
                    pass

            if b.get("text"):
                text_boxes.append({"id": bid, "x": x, "y": y, "w": w, "h": h})

        # collisions among text-bearing boxes
        for i in range(len(text_boxes)):
            for j in range(i + 1, len(text_boxes)):
                if _overlap(text_boxes[i], text_boxes[j]):
                    findings.append(
                        f"{where}: text '{text_boxes[i]['id']}' overlaps text "
                        f"'{text_boxes[j]['id']}' — overlapping/illegible.")
    return findings


def check_pixels(shot: Path, safe_pct: float = DEFAULT_SAFE_PCT
                 ) -> tuple[str, list[str]]:
    """Measure the content bounding box of a rendered frame. Returns
    (status, findings). status is 'SKIPPED' when Pillow is unavailable."""
    try:
        from PIL import Image, ImageChops  # noqa
    except Exception:
        return "SKIPPED", ["Pillow not installed — pixel probe skipped "
                           "(pip install Pillow to enable)."]
    if not shot.exists():
        return "SKIPPED", [f"no frame at {shot}."]

    img = Image.open(shot).convert("RGB")
    fw, fh = img.size
    # Background assumed to be the top-left corner color (scenes use a full-bleed
    # gradient/solid). diff against it; the non-background bbox is the content.
    bg = img.getpixel((0, 0))
    bg_img = Image.new("RGB", img.size, bg)
    diff = ImageChops.difference(img, bg_img).convert("L")
    # threshold: ignore near-background gradient noise
    mask = diff.point(lambda p: 255 if p > 24 else 0)
    bbox = mask.getbbox()  # (l, t, r, b) of content, or None if empty

    findings: list[str] = []
    if bbox is None:
        findings.append(f"{shot.name}: frame appears empty/uniform — no content "
                        f"detected (black/blank scene?).")
        return "REVIEW", findings

    l, t, r, b = bbox
    mx, my = fw * safe_pct, fh * safe_pct
    if l < mx or t < my or r > fw - mx or b > fh - my:
        findings.append(
            f"{shot.name}: content reaches the frame edge / {safe_pct*100:.0f}% "
            f"safe area (content {l},{t}..{r},{b} in {fw}x{fh}) — overflow or "
            f"crowding; pull it inside the safe area.")
    coverage = ((r - l) * (b - t)) / float(fw * fh)
    if coverage < 0.12:
        findings.append(
            f"{shot.name}: content fills only {coverage*100:.0f}% of the frame — "
            f"looks barren/scattered; use the space deliberately.")
    return ("REVIEW" if findings else "PASS"), findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("layout", nargs="?", default=None,
                    help="layout.json geometry contract (see module docstring)")
    ap.add_argument("--shot", default=None,
                    help="a rendered frame PNG for the pixel probe")
    ap.add_argument("--safe-margin-pct", type=float, default=DEFAULT_SAFE_PCT)
    args = ap.parse_args()

    if not args.layout and not args.shot:
        emit({"gate": "layout_probe", "status": "SKIPPED",
              "summary": "nothing to check — pass a layout.json and/or --shot"})
        return EXIT_SKIPPED

    findings: list[str] = []
    notes: list[str] = []
    ran_something = False
    saw_review = False

    if args.layout:
        lp = Path(args.layout)
        if not lp.exists():
            emit({"gate": "layout_probe", "status": "SKIPPED",
                  "summary": f"no layout.json at {lp} — geometry not captured yet"})
            if not args.shot:
                return EXIT_SKIPPED
        else:
            try:
                data = load_json(lp)
            except Exception as e:  # noqa
                emit({"gate": "layout_probe", "status": "ERROR",
                      "summary": f"bad layout.json: {e}"})
                return EXIT_ERROR
            geo = check_geometry(data)
            findings += geo
            ran_something = True
            saw_review = saw_review or bool(geo)

    if args.shot:
        status, pf = check_pixels(Path(args.shot), args.safe_margin_pct)
        if status == "SKIPPED":
            notes += pf
        else:
            findings += pf
            ran_something = True
            saw_review = saw_review or (status == "REVIEW")

    if not ran_something:
        emit({"gate": "layout_probe", "status": "SKIPPED",
              "summary": "no geometry or renderable frame available here; "
                         "capture layout.json or a frame PNG to run this",
              "notes": notes})
        return EXIT_SKIPPED

    emit({
        "gate": "layout_probe",
        "status": "REVIEW" if saw_review else "PASS",
        "findings": findings,
        "notes": notes,
        "summary": (f"{len(findings)} layout/composition issue(s) to fix "
                    f"(overflow / crowding / collision / tiny type)" if findings
                    else "layout OK: nothing overflows, crowds, collides, or is "
                         "below the type floor — now LOOK at the frames too"),
        "advisory_note": "A passing geometry probe is necessary, not sufficient. "
                         "Still inspect representative frames yourself for "
                         "alignment, grouping, and deliberate use of space "
                         "(PLAYBOOK 2.4a / 2.6).",
    })
    return EXIT_REVIEW if saw_review else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
