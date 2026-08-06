#!/usr/bin/env python3
"""
Coordn8 — vendor the free client-side visual libraries LOCALLY.

The video and page get their craft from best-in-class, FREE, no-API JavaScript
libraries. To keep the self-contained/offline contract (the page is one file, a
scene renders with no network), we DOWNLOAD each library once into a local
`_build/vendor/` directory; the agent then INLINES the ones a scene uses directly
into that scene's HTML (and into the final page). After this one fetch, rendering
needs no network at all.

All libraries here are free for commercial use with no key/account/server:
  - GSAP (+ its now-free plugins)  — professional easing, timelines, morphing:
      the single biggest lever for "crafted, not linear-CSS" motion.
  - D3                             — force graphs, tree layouts, data transitions.
  - Rough.js                       — hand-drawn / sketchy aesthetic (warmth).
  - anime.js                       — lightweight keyframe/timeline motion.

We pin exact versions for reproducibility and fetch from jsDelivr (a public CDN
that mirrors npm). If a download fails (no network), we say so specifically and
which visual technique it limits — we never fake a vendored file.

NOTE ON INTEGRITY: we record the sha256 of every downloaded file into
`_build/vendor/manifest.json` so a later build can detect a changed/corrupt
vendored asset. We do NOT hard-pin an expected hash in this script, because the
CDN's minified bytes can legitimately change across patch releases; the manifest
gives provenance without falsely failing a valid file. Verify provenance by
pinning the version (below), which we do.

Usage:
  python3 scripts/render/fetch_visual_libs.py            # fetch all, into _build/vendor
  python3 scripts/render/fetch_visual_libs.py --dry-run  # print plan, fetch nothing
  python3 scripts/render/fetch_visual_libs.py --only gsap d3
  python3 scripts/render/fetch_visual_libs.py --dir path/to/vendor
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Pinned, free, commercial-OK builds. Version pins = reproducibility.
# (Verified free/commercial: GSAP is 100% free incl. plugins as of 2025;
#  D3 BSD, Rough.js MIT, anime.js MIT.)
LIBS = {
    "gsap": {
        "url": "https://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/gsap.min.js",
        "file": "gsap.min.js",
        "why": "easing, master timelines, staggered eased entrances (polish layer)",
        "license": "GSAP standard 'no charge' license (free, commercial OK)",
    },
    "gsap-scrolltrigger": {
        "url": "https://cdn.jsdelivr.net/npm/gsap@3.13.0/dist/ScrollTrigger.min.js",
        "file": "ScrollTrigger.min.js",
        "why": "scroll-driven reveals for the interactive PAGE",
        "license": "GSAP standard 'no charge' license (free, commercial OK)",
    },
    "d3": {
        "url": "https://cdn.jsdelivr.net/npm/d3@7.9.0/dist/d3.min.js",
        "file": "d3.min.js",
        "why": "force-directed dependency graphs, tree layouts, data transitions",
        "license": "BSD-3-Clause (free, commercial OK)",
    },
    "rough": {
        "url": "https://cdn.jsdelivr.net/npm/roughjs@4.6.6/bundled/rough.min.js",
        "file": "rough.min.js",
        "why": "hand-drawn / sketchy aesthetic — warmth over corporate boxes",
        "license": "MIT (free, commercial OK)",
    },
    "anime": {
        "url": "https://cdn.jsdelivr.net/npm/animejs@3.2.2/lib/anime.min.js",
        "file": "anime.min.js",
        "why": "lightweight keyframe/timeline motion where GSAP is overkill",
        "license": "MIT (free, commercial OK)",
    },
}


def log(msg: str) -> None:
    sys.stderr.write(f"[coordn8:vendor] {msg}\n")
    sys.stderr.flush()


def fetch_one(key: str, spec: dict, vendor_dir: Path) -> dict:
    out = vendor_dir / spec["file"]
    if out.exists() and out.stat().st_size > 0:
        data = out.read_bytes()
        return {"lib": key, "status": "already-present", "file": str(out),
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "license": spec["license"], "why": spec["why"]}
    try:
        req = urllib.request.Request(spec["url"],
                                     headers={"User-Agent": "coordn8-vendor"})
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 (https pinned)
            data = r.read()
        if not data:
            raise ValueError("empty response")
        vendor_dir.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"lib": key, "status": "fetched", "file": str(out),
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "license": spec["license"], "why": spec["why"]}
    except (urllib.error.URLError, ValueError, OSError) as e:
        return {"lib": key, "status": "fetch-failed", "file": None,
                "reason": f"{type(e).__name__}: {e}",
                "url": spec["url"], "license": spec["license"], "why": spec["why"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="_build/vendor",
                    help="where to vendor the JS files (default _build/vendor)")
    ap.add_argument("--only", nargs="*", default=None,
                    help=f"subset of {sorted(LIBS)}")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan; download nothing")
    args = ap.parse_args()

    want = list(args.only) if args.only else list(LIBS)
    unknown = [k for k in want if k not in LIBS]
    if unknown:
        log(f"unknown lib(s): {unknown}; known: {sorted(LIBS)}")
        return 2

    vendor_dir = Path(args.dir)

    if args.dry_run:
        plan = [{"lib": k, "url": LIBS[k]["url"], "file": LIBS[k]["file"],
                 "why": LIBS[k]["why"], "license": LIBS[k]["license"]}
                for k in want]
        print(json.dumps({"tool": "fetch_visual_libs", "dry_run": True,
                          "vendor_dir": str(vendor_dir), "plan": plan}, indent=2))
        log(f"would vendor {len(plan)} lib(s) into {vendor_dir} (nothing downloaded)")
        return 0

    results = [fetch_one(k, LIBS[k], vendor_dir) for k in want]
    ok = [r for r in results if r["status"] in ("fetched", "already-present")]
    failed = [r for r in results if r["status"] == "fetch-failed"]

    manifest = vendor_dir / "manifest.json"
    if ok:
        vendor_dir.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(
            {"vendored": ok, "failed": failed}, indent=2), encoding="utf-8")

    summary = {
        "tool": "fetch_visual_libs",
        "vendor_dir": str(vendor_dir),
        "vendored": ok,
        "failed": failed,
        "manifest": str(manifest) if ok else None,
        "guidance": (
            "Inline the ones each scene uses into that scene's HTML (keeps the "
            "self-contained/offline contract). All are free & commercial-OK."
            if not failed else
            "Some libraries could not be downloaded (see 'failed' with the "
            "specific reason). Those visual techniques are limited until fetched; "
            "the rest are available. Never fake a vendored file."),
    }
    print(json.dumps(summary, indent=2))
    log(summary["guidance"])
    # Advisory: partial success is fine — the agent uses what vendored.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
