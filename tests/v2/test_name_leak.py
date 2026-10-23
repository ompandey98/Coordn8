#!/usr/bin/env python3
"""Name-leak test: zero reference-project names anywhere in the package.

The package must never contain the names, so this test ships only HASHES of the
forbidden tokens (sha256, first 20 hex). When the local provenance file or the
internal sources list is present, their plain names are also used for a stronger
substring scan. PROVENANCE.local.md itself is the one excluded file.

    python3 tests/v2/test_name_leak.py [--root DIR]     # DIR = an extracted release zip
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite  # noqa: E402

HASHES = {'195aa382dc2f00159e86', '30504b6a561add613917', '3b3857e8e0b2c9139017', '5e5ac563587d83594c0a',
          '67ad5c0b3214bf7e0757', '6824b306d8038a130d1d', '697684d5a68be7f73ef2', '6c249cb5506b2764d296',
          '77b0bbfbff8e33210a80', '86876b2e4ff7f4a516b9', '969d69f21d22096693ec', '97fc49297c3bffdfc0ed',
          '992a1cda8d4a66edf26f', 'a8cc5c5970651c735ff9', 'b4b2bac2ba20b9329267', 'b8e38c1ffb448362f2c3',
          'ccb0921c6985017723cd', 'df7cdafcf49a9d4816b9', 'e8dcf73b055db29bc0a5',
          'eefb781a3bf3952b58d1', 'f84cf8c4c36737165a14', 'ff7d986cb3f9d7c925aa', 'ffe49eb4a21b0f6b100e'}
EXCLUDE_FILES = {"PROVENANCE.local.md"}
SKIP_DIRS = {".git", "node_modules", "__pycache__"}
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:20]


def candidates(text: str):
    toks = TOKEN.findall(text.lower())
    for i, t in enumerate(toks):
        parts = t.split("-")
        for a in range(len(parts)):
            for b in range(a + 1, len(parts) + 1):
                yield "-".join(parts[a:b])
        if i + 1 < len(toks):
            yield t + " " + toks[i + 1]


def plain_names() -> list[str]:
    """Optional stronger list from internal files that never ship."""
    srcs = [ROOT / "PROVENANCE.local.md",
            Path(os.environ.get("C8_SOURCES_INTERNAL",
                                str(ROOT.parent / "Coordn8_v2_handoff" / "_SOURCES_INTERNAL.md")))]
    names = set()
    for s in srcs:
        if s.exists():
            for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", s.read_text(encoding="utf-8")):
                for n in m.groups():
                    if len(n) >= 4:
                        names.add(n.lower().rstrip("."))
    return sorted(names)


def scan(root: Path) -> list[str]:
    hits = []
    plain = [n for n in plain_names() if h(n) in HASHES or len(n) >= 6]
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
            continue
        if p.name in EXCLUDE_FILES or p.suffix in (".zip", ".png", ".jpg", ".mp4", ".wav", ".onnx", ".bin", ".woff2", ".ttf"):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for c in candidates(text + " " + str(rel)):
            if h(c) in HASHES:
                hits.append(f"{rel}: hashed name token")
                break
        low = text.lower()
        for n in plain:
            if n in low:
                hits.append(f"{rel}: '{n[:3]}…'")
    return sorted(set(hits))


def main() -> int:
    root = ROOT
    if "--root" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--root") + 1]).resolve()
    s = Suite("name_leak")
    s.check("the hashed name list is loaded (23 tokens)", len(HASHES) == 23, str(len(HASHES)))
    s.check("the scanner detects a seeded name (self-test)",
            any(h(c) in HASHES for c in candidates("see " + "weil" + "anx for details")), "")
    hits = scan(root)
    s.check("zero reference names anywhere in the package (except the local provenance file)",
            not hits, "; ".join(hits[:20]))
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
