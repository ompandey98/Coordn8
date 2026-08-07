#!/usr/bin/env python3
"""
Coordn8 — vendor a free, open-licensed ICON LIBRARY locally (crisp inline SVG).

Beautiful explainer visuals lean on good raw material — clean, consistent icons
being the biggest single lift over ad-hoc boxes. This vendors the icons the same
way `fetch_visual_libs.py` vendors GSAP/D3/etc.: download once into a local
`_build/vendor/icons/` directory, then the agent INLINES the exact icons a scene
or page uses as SVG. Vector, not PNG, on purpose — one source renders crisp at
ANY size the video/page needs (a 512px hero mark and a 24px inline chip come from
the same file, no blur, no per-size asset), and every icon carries an explicit
`viewBox` so its dimensions are always known. After this one fetch, rendering
needs no network and the self-contained/offline contract holds (0.4a rule 10).

This is the RELIABLE, bundled path (an enhancement, exactly like the JS libs —
`setup_env.py` runs it, a failure is a quality note, never a video blocker). It
does NOT replace the *suggestion* to web-search for additional/nicer assets when
the environment allows (PLAYBOOK 0.4a rule 10) — it's the always-available floor
that guarantees a strong, license-clean icon set even with no other web access.

Source: the Iconify project's per-set JSON packages on jsDelivr
(`@iconify-json/<set>`), which bundle every icon of a set as `{name: {body}}`
with a top-level default `width`/`height`. Iconify is a redistribution layer; the
icons keep their ORIGINAL upstream licenses, all open/permissive and recorded per
set below and in the manifest. Only open-licensed sets are included — no
copyrighted or watermarked material (1.5 / 2.6).

Usage:
  python3 scripts/render/fetch_icons.py                     # vendor default sets
  python3 scripts/render/fetch_icons.py --dry-run           # print plan, fetch nothing
  python3 scripts/render/fetch_icons.py --only lucide tabler
  python3 scripts/render/fetch_icons.py --dir path/to/vendor/icons
  # after fetching, extract one icon as a standalone inline SVG to drop into a scene:
  python3 scripts/render/fetch_icons.py render --set lucide --name database \
      --size 96 --color "#e5e7eb" --out _build/assets/database.svg
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Pinned, open-licensed icon sets (versions + licenses VERIFIED against the
# Iconify collections registry). Each `url` is the set's full icons.json:
#   {"prefix", "icons": {name: {"body": "<inner svg>"}}, "width", "height"}
# Vector SVG scales crisp to any dimension the video/page asks for.
ICON_SETS = {
    "lucide": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/lucide@1/icons.json",
        "file": "lucide.json",
        "why": "clean, consistent line icons — the modern default; general UI/tech",
        "license": "ISC (free, commercial OK)",
        "homepage": "https://lucide.dev",
    },
    "tabler": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/tabler@1/icons.json",
        "file": "tabler.json",
        "why": "~6000 uniform line icons — widest coverage for technical concepts",
        "license": "MIT (free, commercial OK)",
        "homepage": "https://tabler.io/icons",
    },
    "heroicons": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/heroicons@1/icons.json",
        "file": "heroicons.json",
        "why": "polished UI icons (outline + solid) from the Tailwind team",
        "license": "MIT (free, commercial OK)",
        "homepage": "https://heroicons.com",
    },
    "material-symbols": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/material-symbols@1/icons.json",
        "file": "material-symbols.json",
        "why": "Google's huge filled/rounded set — when a solid glyph reads better",
        "license": "Apache-2.0 (free, commercial OK)",
        "homepage": "https://fonts.google.com/icons",
    },
    "logos": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/logos@1/icons.json",
        "file": "logos.json",
        "why": "FULL-COLOR official brand/SERVICE marks — AWS services (aws-s3, "
               "aws-lambda, aws-dynamodb…), GCP/Azure, languages, DBs, tools. THIS "
               "is the set to use for architecture diagrams: the real S3 mark, not "
               "a generic 'bucket'.",
        "license": "CC0-1.0 (public domain; marks are third-party trademarks — "
                   "use to identify the service/tech, never imply endorsement)",
        "homepage": "https://svgporn.com",
    },
    "simple-icons": {
        "url": "https://cdn.jsdelivr.net/npm/@iconify-json/simple-icons@1/icons.json",
        "file": "simple-icons.json",
        "why": "MONOCHROME brand/product/tech glyphs (languages, DBs, clouds, tools) "
               "— use when a single-color logo fits the design better than the "
               "full-color 'logos' mark",
        "license": "CC0-1.0 (public domain; logos are third-party trademarks — "
                   "use to identify the tech, never imply endorsement)",
        "homepage": "https://simpleicons.org",
    },
}

# Sensible default: two general-purpose line sets for generic concepts, PLUS the
# full-color 'logos' set so architecture/infra diagrams get the CORRECT official
# service marks (the real Amazon S3 logo, not a keyword-matched bathroom bucket).
# The big filled set + monochrome brand glyphs stay opt-in via --only.
DEFAULT_SETS = ["lucide", "tabler", "logos"]

# Common concept -> canonical icon name, so the agent doesn't keyword-guess its
# way to a wrong look-alike (the "bucket" != Amazon S3 failure). This is a small,
# high-value seed for the things most often drawn wrong in architecture diagrams;
# `search` covers everything else. Entries are (set, name) and are VERIFIED to
# exist in the pinned sets. Never silently fall back to a generic shape for a
# named service — look it up.
CANONICAL = {
    # AWS — full-color official marks live in `logos`
    "amazon s3": ("logos", "aws-s3"), "s3": ("logos", "aws-s3"),
    "aws lambda": ("logos", "aws-lambda"), "lambda": ("logos", "aws-lambda"),
    "dynamodb": ("logos", "aws-dynamodb"),
    "amazon ec2": ("logos", "aws-ec2"), "ec2": ("logos", "aws-ec2"),
    "aws": ("logos", "aws"),
    # other clouds / common infra
    "google cloud": ("logos", "google-cloud"), "gcp": ("logos", "google-cloud"),
    "azure": ("logos", "microsoft-azure"),
    "kubernetes": ("logos", "kubernetes"), "k8s": ("logos", "kubernetes"),
    "docker": ("logos", "docker-icon"),
    "postgres": ("logos", "postgresql"), "postgresql": ("logos", "postgresql"),
    "redis": ("logos", "redis"), "kafka": ("logos", "kafka-icon"),
}


def log(msg: str) -> None:
    sys.stderr.write(f"[coordn8:icons] {msg}\n")
    sys.stderr.flush()


def fetch_one(key: str, spec: dict, vendor_dir: Path) -> dict:
    out = vendor_dir / spec["file"]
    if out.exists() and out.stat().st_size > 0:
        data = out.read_bytes()
        n = _count_icons(data)
        return {"set": key, "status": "already-present", "file": str(out),
                "bytes": len(data), "icons": n,
                "sha256": hashlib.sha256(data).hexdigest(),
                "license": spec["license"], "why": spec["why"]}
    try:
        req = urllib.request.Request(spec["url"],
                                     headers={"User-Agent": "coordn8-icons"})
        with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 (https pinned)
            data = r.read()
        if not data:
            raise ValueError("empty response")
        # sanity: it must parse and look like an Iconify set, else don't vendor it
        n = _count_icons(data)
        if n <= 0:
            raise ValueError("no icons in payload (not an Iconify set?)")
        vendor_dir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"set": key, "status": "fetched", "file": str(out),
                "bytes": len(data), "icons": n,
                "sha256": hashlib.sha256(data).hexdigest(),
                "license": spec["license"], "why": spec["why"]}
    except (urllib.error.URLError, ValueError, OSError, json.JSONDecodeError) as e:
        return {"set": key, "status": "fetch-failed", "file": None,
                "reason": f"{type(e).__name__}: {e}",
                "url": spec["url"], "license": spec["license"], "why": spec["why"]}


def _count_icons(data: bytes) -> int:
    try:
        return len(json.loads(data).get("icons", {}))
    except (ValueError, TypeError):
        return -1


# --- render: turn one vendored icon into a standalone inline SVG --------------

def render_icon(vendor_dir: Path, set_key: str, name: str, size: int,
                color: str) -> str | None:
    """Build a complete, self-contained <svg> string for one icon, sized and
    colored, ready to inline into a scene/page. Returns None if not found.

    Iconify stores each icon's inner markup in `icons[name]["body"]`, drawn in
    the set's coordinate box (top-level `width`/`height`, per-icon override
    allowed). `currentColor` in the body inherits our `color`. We emit a real
    `viewBox` so it scales crisp to any `size` (clear, known dimensions).
    """
    spec = ICON_SETS.get(set_key)
    if not spec:
        log(f"unknown set {set_key!r}; known: {sorted(ICON_SETS)}")
        return None
    path = vendor_dir / spec["file"]
    if not path.exists():
        log(f"set {set_key!r} not vendored yet at {path} — run fetch first.")
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    icons = data.get("icons", {})
    icon = icons.get(name)
    if icon is None:
        # be helpful: surface a few near-name suggestions
        near = [k for k in icons if name in k][:8]
        log(f"icon {set_key}:{name!r} not found."
            + (f" did you mean: {near}?" if near else ""))
        return None
    vw = icon.get("width", data.get("width", 24))
    vh = icon.get("height", data.get("height", 24))
    body = icon["body"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
            f'height="{round(size * vh / vw)}" viewBox="0 0 {vw} {vh}" '
            f'fill="none" color="{color}" role="img" '
            f'aria-label="{set_key} {name}">{body}</svg>')


# --- search: find the CORRECT canonical icon, don't keyword-guess ------------
#
# The "bathroom bucket instead of Amazon S3" failure happens when the agent maps
# a concept to an icon by naive keyword ("bucket") against a generic line set.
# For anything with a canonical visual identity — a cloud service, a product, a
# brand, a language — there is ONE right mark, and it must be looked up, not
# guessed. `resolve_concept` checks the curated CANONICAL table first, then falls
# back to a name search across the vendored sets (logos/simple-icons first, since
# that's where service/brand marks live).

def search_vendored(vendor_dir: Path, query: str, limit: int = 20) -> list[dict]:
    """Substring-search icon names across every vendored set. Brand/service sets
    (logos, simple-icons) are searched first so a service query surfaces the real
    mark before a generic line-icon look-alike."""
    q = query.lower().replace(" ", "-")
    order = ["logos", "simple-icons", "material-symbols", "heroicons",
             "lucide", "tabler"]
    hits: list[dict] = []
    for key in order:
        spec = ICON_SETS.get(key)
        if not spec:
            continue
        path = vendor_dir / spec["file"]
        if not path.exists():
            continue
        try:
            icons = json.loads(path.read_text(encoding="utf-8")).get("icons", {})
        except (ValueError, OSError):
            continue
        for name in icons:
            if q in name.lower():
                hits.append({"set": key, "name": name, "ref": f"{key}:{name}"})
                if len(hits) >= limit:
                    return hits
    return hits


def resolve_concept(vendor_dir: Path, concept: str) -> dict:
    """Map a plain concept ('Amazon S3', 'postgres') to the CORRECT icon ref.
    Returns {canonical: [...], search: [...]} — canonical is the curated,
    verified answer (use it); search is the ranked fallback."""
    key = concept.lower().strip()
    canonical = []
    if key in CANONICAL:
        st, nm = CANONICAL[key]
        canonical.append({"set": st, "name": nm, "ref": f"{st}:{nm}",
                          "source": "curated"})
    return {"concept": concept,
            "canonical": canonical,
            "search": search_vendored(vendor_dir, concept)}


def cmd_search(args: argparse.Namespace) -> int:
    vendor_dir = Path(args.dir)
    res = resolve_concept(vendor_dir, args.query)
    if not res["canonical"] and not res["search"]:
        log(f"no vendored icon matches {args.query!r}. If a service/brand mark is "
            f"needed, vendor the 'logos' set (fetch_icons.py --only logos) or "
            f"web-search Iconify (https://icon-sets.iconify.design) for the exact "
            f"name, then render it. Never substitute a generic look-alike for a "
            f"named service (the 'bucket' != Amazon S3 failure).")
        print(json.dumps(res, indent=2))
        return 1
    print(json.dumps(res, indent=2))
    if res["canonical"]:
        log(f"canonical mark for {args.query!r}: {res['canonical'][0]['ref']} "
            f"(use this — verified correct, not a keyword guess)")
    else:
        log(f"no curated mark for {args.query!r}; best name matches: "
            f"{[h['ref'] for h in res['search'][:5]]} — VERIFY the top hit "
            f"actually depicts the thing before using it.")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    vendor_dir = Path(args.dir)
    svg = render_icon(vendor_dir, args.set, args.name, args.size, args.color)
    if svg is None:
        return 1
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(svg, encoding="utf-8")
        log(f"wrote {args.set}:{args.name} -> {out} ({args.size}px, inline SVG)")
    else:
        sys.stdout.write(svg + "\n")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    want = list(args.only) if args.only else list(DEFAULT_SETS)
    unknown = [k for k in want if k not in ICON_SETS]
    if unknown:
        log(f"unknown set(s): {unknown}; known: {sorted(ICON_SETS)}")
        return 2

    vendor_dir = Path(args.dir)

    if args.dry_run:
        plan = [{"set": k, "url": ICON_SETS[k]["url"], "file": ICON_SETS[k]["file"],
                 "why": ICON_SETS[k]["why"], "license": ICON_SETS[k]["license"]}
                for k in want]
        print(json.dumps({"tool": "fetch_icons", "dry_run": True,
                          "vendor_dir": str(vendor_dir), "plan": plan}, indent=2))
        log(f"would vendor {len(plan)} icon set(s) into {vendor_dir} "
            f"(nothing downloaded)")
        return 0

    results = [fetch_one(k, ICON_SETS[k], vendor_dir) for k in want]
    ok = [r for r in results if r["status"] in ("fetched", "already-present")]
    failed = [r for r in results if r["status"] == "fetch-failed"]

    manifest = vendor_dir / "icons_manifest.json"
    if ok:
        vendor_dir.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(
            {"vendored": ok, "failed": failed,
             "note": "Icons keep their upstream open licenses (recorded per set). "
                     "Inline only the icons a scene/page uses, as SVG, to keep the "
                     "self-contained/offline contract. Simple-icons logos identify "
                     "the tech; never imply endorsement."}, indent=2),
            encoding="utf-8")

    total_icons = sum(r.get("icons", 0) for r in ok if r.get("icons", 0) > 0)
    summary = {
        "tool": "fetch_icons",
        "vendor_dir": str(vendor_dir),
        "vendored": ok,
        "failed": failed,
        "total_icons_available": total_icons,
        "manifest": str(manifest) if ok else None,
        "guidance": (
            f"{total_icons} open-licensed icons vendored. Extract one with "
            "`fetch_icons.py render --set <set> --name <icon> --size <px>` and "
            "INLINE the SVG into the scene/page (keeps offline/self-contained). "
            "Vector scales crisp at any size."
            if not failed else
            "Some icon sets could not be downloaded (see 'failed' with the "
            "specific reason). Icons are an ENHANCEMENT — the base video/page "
            "still ships; those visuals just use the vendored JS/drawn shapes "
            "until fetched. Never fake a vendored asset."),
    }
    print(json.dumps(summary, indent=2))
    log(summary["guidance"])
    # Advisory: partial success is fine — the agent uses whatever vendored.
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Vendor open-licensed icon sets locally (inline SVG).")
    ap.add_argument("--dir", default="_build/vendor/icons",
                    help="where to vendor the icon JSON (default _build/vendor/icons)")
    ap.add_argument("--only", nargs="*", default=None,
                    help=f"subset of {sorted(ICON_SETS)} (default: {DEFAULT_SETS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan; download nothing")
    sub = ap.add_subparsers(dest="cmd")

    r = sub.add_parser("render", help="extract one vendored icon as inline SVG")
    r.add_argument("--set", required=True, help=f"one of {sorted(ICON_SETS)}")
    r.add_argument("--name", required=True, help="icon name within the set")
    r.add_argument("--size", type=int, default=64, help="pixel width (default 64)")
    r.add_argument("--color", default="currentColor",
                   help="stroke/fill color (default currentColor)")
    r.add_argument("--out", default=None, help="write SVG here (else stdout)")
    r.add_argument("--dir", default="_build/vendor/icons",
                   help="vendored-icons dir (default _build/vendor/icons)")

    s = sub.add_parser("search",
                       help="find the CORRECT canonical icon for a concept "
                            "(service/brand/tech) instead of keyword-guessing")
    s.add_argument("query", help="concept, e.g. 'Amazon S3' or 'postgres'")
    s.add_argument("--dir", default="_build/vendor/icons",
                   help="vendored-icons dir (default _build/vendor/icons)")

    args = ap.parse_args()
    if args.cmd == "render":
        return cmd_render(args)
    if args.cmd == "search":
        return cmd_search(args)
    return cmd_fetch(args)


if __name__ == "__main__":
    sys.exit(main())
