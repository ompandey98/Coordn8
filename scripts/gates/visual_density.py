#!/usr/bin/env python3
"""
Coordn8 gate — VISUAL DENSITY & RELEVANCE (both deliverables).

The single most common quality failure is NOT a wrong fact — it is a low-effort,
under-visualized deliverable: a video that is a static text box with two shapes
and no motion, or a page that is a slideshow of boxes-and-bullets. That happened
because "make it rich" was never a STRUCTURAL, checked requirement. This gate
makes it one. It reads the VISUAL PLAN (the storyboard for the video, the page
plan for the page) BEFORE rendering and fails a plan that is sparse, static,
filler, generic, or space-wasting — so the fix is cheap (redesign on paper), not
after five cursing iterations of burned frames. (PLAYBOOK 0.4a / 2.1a / 1.1a.)

What it enforces (the visual doctrine, made mechanical):
  1  Show-don't-tell   every scene/section has a non-text `primary_visual`.
  2  Earns its place   every element has an `explains`; decoration-with-no-
                       referent (random stars/flowers/pigs/butterflies) is flagged.
  3  Relevant depiction not a page/video made mostly of generic boxes.
  4  Full-frame use    composition spans the frame/width, not a centered quarter.
  5  Motion (video)    every scene has real motion under narration; the page's
                       equivalent is an interaction / progressive build.
  6  Density floor     enough meaningful visual elements per scene, and overall,
                       to clear an explainer-grade bar (not a slideshow).

ADVISORY: exit 1 (REVIEW) means "these scenes/sections look thin or off — look at
them and redesign before rendering." It is a lead, not a verdict. If the helper
misjudges THIS project (a deliberate single-hero focal beat, a domain form it
doesn't recognize), the agent overrides with its own judgment — it never forces
padding or fake motion to ship, and a PASS never excuses a plan that still reads
low-effort to your own eyes.

Data contracts (DEFAULT shapes; deviate if the project needs to, but keep the
fields this gate reads):

  video  — _build/storyboard.json  (PLAYBOOK 2.1a)
    {"fps":30,"frame":{"width":1920,"height":1080},
     "scenes":[{"id":"s3","beat":"machinery","primary_visual":"...",
       "elements":[{"id":"token","kind":"data","explains":"...",
                    "ref":"types.py:12","region":"left-third","motion":"..."}],
       "moving_element_count":2,"uses_regions":["left-third","center"],
       "screenshot_worthy":true}]}

  page   — _build/page_plan.json   (PLAYBOOK 1.1a)
    {"sections":[{"id":"architecture","idea":"...","primary_visual":"...",
       "elements":[{"id":"map","kind":"diagram","explains":"...","ref":"a.py:1",
                    "region":"full"}],
       "interaction":"hover a component to see its config",
       "uses_full_width":true,
       "not_video_scene":true}]}   # this section is a standalone reader
                                   # destination, NOT a transcribed video beat.

  Two page-only behaviors (PLAYBOOK 1.1a / 1.1 item 6):
    - A section with "role":"embed"/"watch" (or "is_video_embed":true) is the
      OPTIONAL video player tab. It is EXEMPT from the carry-your-own-visuals
      checks (its job is to host the sibling mp4 with speed controls) — but it
      must not be load-bearing; the medium-fit critic (R.1) enforces that.
    - A section flagged "not_video_scene":false (or with a "mirrors_scene" ref)
      is calling ITSELF a re-skinned video beat, which is the exact failure the
      page must avoid — the gate flags it so it's redesigned as a real reader
      destination, not the video's timeline replayed as prose.

Usage:
    python3 scripts/gates/visual_density.py _build/storyboard.json
    python3 scripts/gates/visual_density.py _build/page_plan.json --kind page
    python3 scripts/gates/visual_density.py plan.json --min-visuals 2 --min-moving 1
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import (EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED,  # noqa: E402
                        emit, load_json)

# A primary_visual that is really just text is not a visual (doctrine 1).
TEXT_ONLY_RE = re.compile(
    r"^\s*(a\s+|an\s+|the\s+|just\s+|plain\s+|some\s+|simple\s+)*"
    r"(text\s*box|textbox|text|bullet(?:\s*(?:list|point)s?)?|paragraph|prose|"
    r"title\s*card|title|heading|subtitle|caption|words|sentence|quote|"
    r"list|label)\s*$",
    re.IGNORECASE)

# Element kinds that are text/support, not a visual in their own right.
TEXT_KINDS = {"text", "label", "caption", "title", "heading", "subtitle",
              "bullet", "paragraph", "prose", "quote", "note"}

# Generic filler shapes — the fallback of last resort (doctrine 3). A scene/page
# made mostly of these is flagged: reach for the domain-specific depiction.
GENERIC_KINDS = {"box", "rect", "rectangle", "square", "shape", "blob",
                 "circle", "oval", "generic", "placeholder"}

# Decoration-with-no-referent (doctrine 2). These are LEADS: an element whose
# name/kind looks purely decorative is flagged so the agent confirms it teaches
# something real (a rating "star" tied to a score is fine; a sprinkled one isn't).
DECORATION_RE = re.compile(
    r"\b(star|flower|petal|pig|butterfly|sparkle|glitter|confetti|balloon|"
    r"unicorn|rainbow|doodle|mascot|emoji|heart(?!\s*beat)|smiley|firework)s?\b",
    re.IGNORECASE)

# Region tokens that mean "only the middle of the frame". A scene living only
# here (with nothing elsewhere) is the "one small visual marooned in the center
# with dead margins" failure (doctrine 4) — unless it's a deliberate hero beat.
CENTER_ONLY = {"center", "centre", "middle", "center-center", "central"}

# A page section that is deliberately the optional video-embed "Watch" tab
# (PLAYBOOK 1.1 item 6). It is exempt from the "carry your own visuals" checks —
# its whole job is to host the sibling mp4 in a player with speed controls — but
# it must NOT be load-bearing (the page teaches fully without it).
EMBED_ROLES = {"embed", "video_embed", "video-embed", "watch", "player",
               "video_player"}


def _txt(v) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _is_text_only_visual(primary: str) -> bool:
    p = primary.strip()
    return (not p) or bool(TEXT_ONLY_RE.match(p))


def analyze_unit(unit: dict, idx: int, kind: str,
                 min_visuals: int, min_moving: int) -> dict:
    """kind is 'video' (a scene) or 'page' (a section). Returns findings."""
    uid = unit.get("id") or f"#{idx}"
    label = unit.get("beat") or unit.get("idea") or unit.get("title") or uid
    primary = _txt(unit.get("primary_visual"))
    elements = unit.get("elements") or []
    findings: list[str] = []

    # PAGE ONLY: the optional video-embed "Watch" tab is exempt from carrying its
    # own visuals — its job is to host the sibling mp4 with speed controls (1.1
    # item 6). It must not be load-bearing; that's the medium-fit critic's job
    # (R.1), not this gate's. Recognize it and short-circuit the density checks.
    role = _txt(unit.get("role")).strip().lower()
    if kind == "page" and (role in EMBED_ROLES
                           or bool(unit.get("is_video_embed"))):
        return {
            "id": uid, "label": label, "elements": len(elements),
            "meaningful_visuals": 0, "generic_boxes": 0,
            "findings": [], "ok": True, "note": "video-embed tab (exempt): must "
            "stay an OPTIONAL guest — the page teaches fully without it (1.1a)."}

    # 1 — Show, don't tell: a non-text primary visual is mandatory.
    if _is_text_only_visual(primary):
        findings.append(
            f"no non-text primary_visual — {'scene' if kind=='video' else 'section'} "
            f"'{label}' is carried by text/box only (doctrine 1: show, don't tell).")

    # Classify elements.
    meaningful_visuals = 0     # has an explains AND is a visual (not text/support)
    generic = 0
    for e in elements:
        ekind = _txt(e.get("kind")).strip().lower()
        explains = _txt(e.get("explains")).strip()
        eid = _txt(e.get("id")) or ekind or "?"
        is_text = ekind in TEXT_KINDS

        # 2 — every element earns its place: needs an explains mapping.
        if not explains:
            findings.append(
                f"element '{eid}' has no `explains` — filler with no referent "
                f"(doctrine 2). Either tie it to a specific thing being explained "
                f"or cut it.")
        elif not is_text:
            meaningful_visuals += 1

        # 2 — decoration-with-no-referent (random stars/flowers/pigs/butterflies).
        blob = f"{eid} {ekind} {_txt(e.get('motion'))} {primary}"
        if DECORATION_RE.search(f"{eid} {ekind}"):
            findings.append(
                f"element '{eid}' (kind '{ekind}') looks decorative — confirm it "
                f"depicts a real referent; random stars/flowers/pigs/butterflies "
                f"are banned as clutter (doctrine 2).")

        # 3 — generic-box tally.
        if ekind in GENERIC_KINDS:
            generic += 1

    # 3 — most-relevant depiction, not a wall of generic boxes.
    if elements and generic / len(elements) > 0.6:
        findings.append(
            f"{generic}/{len(elements)} elements are generic boxes/shapes — reach "
            f"for the domain-specific depiction (a queue as a queue, a cache as "
            f"filling slots…); generic-box is the last resort (doctrine 3).")

    # 6 — density floor: enough meaningful visual elements to be explainer-grade.
    if meaningful_visuals < min_visuals:
        findings.append(
            f"only {meaningful_visuals} meaningful visual element(s) "
            f"(floor {min_visuals}) — too sparse; this reads as a slideshow, not "
            f"an explainer (doctrine 6).")

    if kind == "video":
        # 5 — real motion under narration (no static holds).
        declared = int(unit.get("moving_element_count") or 0)
        actual_motion = sum(1 for e in elements if _txt(e.get("motion")).strip())
        moving = max(declared, actual_motion)
        if moving < min_moving:
            findings.append(
                f"no motion planned ({moving} moving element(s), floor "
                f"{min_moving}) — a static hold under narration (doctrine 5). "
                f"Something must move/build/travel while the beat is spoken.")
        elif declared and actual_motion == 0:
            findings.append(
                f"moving_element_count={declared} but NO element has a `motion` "
                f"spec — the declared motion isn't real. Add per-element motion.")

        # 4 — full-frame use.
        regions = [r.strip().lower() for r in (unit.get("uses_regions") or [])
                   if _txt(r).strip()]
        if not regions:
            findings.append(
                "uses_regions is empty — declare which regions of the frame the "
                "composition spans (doctrine 4: use the whole frame).")
        elif set(regions) <= CENTER_ONLY:
            findings.append(
                "composition lives only in the center — the 'one small visual "
                "marooned with dead margins' failure (doctrine 4). Spread related "
                "elements across the frame, OR confirm this is a deliberate hero "
                "beat.")
    else:  # page
        # 5 — the page's motion equivalent: interaction / progressive build.
        interaction = _txt(unit.get("interaction")).strip()
        if not interaction:
            findings.append(
                "no `interaction` — the page's equivalent of motion is a "
                "hover/step/toggle/scrolly reveal the reader drives (doctrine 5). "
                "A purely static section is not acceptable.")
        # 4 — full-width use.
        uses_full = unit.get("uses_full_width")
        regions = [r.strip().lower() for r in (unit.get("uses_regions") or [])
                   if _txt(r).strip()]
        if uses_full is False or (uses_full is None and set(regions) <= CENTER_ONLY
                                  and regions):
            findings.append(
                "section does not use the full width — content stranded in a "
                "narrow center strip with dead margins (doctrine 4).")

        # Medium-fit: the page must be a reader, not the video re-skinned (1.1a).
        # If the plan self-declares this section is a transcribed video beat
        # (not_video_scene: false, or a mirrors_scene ref), that's the failure the
        # medium-fit critic (R.1) exists to catch — flag it here as a lead too.
        if unit.get("not_video_scene") is False or _txt(unit.get("mirrors_scene")).strip():
            findings.append(
                "section is marked as a re-skinned video beat (mirrors a scene / "
                "not a standalone reader destination) — the page must stand on its "
                "own as a compact reader, not replay the video's timeline (1.1a). "
                "Redesign it around the page's own information architecture.")

    return {
        "id": uid,
        "label": label,
        "elements": len(elements),
        "meaningful_visuals": meaningful_visuals,
        "generic_boxes": generic,
        "findings": findings,
        "ok": not findings,
    }


def detect_kind(data: dict, forced: str | None) -> str | None:
    if forced:
        return forced
    if isinstance(data.get("scenes"), list):
        return "video"
    if isinstance(data.get("sections"), list):
        return "page"
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="storyboard.json (video) or page_plan.json (page)")
    ap.add_argument("--kind", choices=("video", "page"), default=None,
                    help="force the plan kind (else auto-detected from shape)")
    ap.add_argument("--min-visuals", type=int, default=2,
                    help="min meaningful visual elements per scene/section")
    ap.add_argument("--min-moving", type=int, default=1,
                    help="min moving elements per video scene")
    args = ap.parse_args()

    path = Path(args.plan)
    if not path.exists():
        emit({"gate": "visual_density", "status": "SKIPPED",
              "summary": f"no visual plan at {path} — build the storyboard/"
                         f"page_plan before rendering (PLAYBOOK 2.1a / 1.1a)"})
        return EXIT_SKIPPED
    try:
        data = load_json(path)
    except Exception as e:
        emit({"gate": "visual_density", "status": "ERROR",
              "summary": f"could not parse {path}: {e}"})
        return EXIT_ERROR

    kind = detect_kind(data, args.kind)
    if kind is None:
        emit({"gate": "visual_density", "status": "ERROR",
              "summary": "cannot tell if this is a video storyboard (needs "
                         "'scenes'[]) or a page plan (needs 'sections'[]); pass "
                         "--kind"})
        return EXIT_ERROR

    units = data.get("scenes" if kind == "video" else "sections") or []
    if not units:
        emit({"gate": "visual_density", "status": "SKIPPED",
              "summary": f"plan has no {'scenes' if kind=='video' else 'sections'}"
                         f"[] — nothing to check yet"})
        return EXIT_SKIPPED

    results = [analyze_unit(u, i, kind, args.min_visuals, args.min_moving)
               for i, u in enumerate(units)]
    bad = [r for r in results if not r["ok"]]
    total_visuals = sum(r["meaningful_visuals"] for r in results)
    avg_visuals = round(total_visuals / len(results), 2) if results else 0

    emit({
        "gate": "visual_density",
        "status": "REVIEW" if bad else "PASS",
        "kind": kind,
        "units_checked": len(results),
        "units_flagged": len(bad),
        "avg_meaningful_visuals_per_unit": avg_visuals,
        "results": results,
        "summary": (
            f"{len(bad)}/{len(results)} {kind} "
            f"{'scene' if kind=='video' else 'section'}(s) look thin/static/"
            f"filler/generic — redesign the plan BEFORE rendering" if bad
            else f"all {len(results)} {kind} "
                 f"{'scene' if kind=='video' else 'section'}(s) clear the "
                 f"visual-density bar (avg {avg_visuals} meaningful visuals each)"
        ),
        "advisory_note": "REVIEW is a lead, not a hard fail. Fix by designing a "
                         "richer, more relevant visual — never by adding "
                         "decoration or faking a `motion`/`explains` field. A "
                         "deliberate single-hero focal beat is a valid override.",
    })
    return EXIT_REVIEW if bad else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
