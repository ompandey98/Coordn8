#!/usr/bin/env python3
"""The recorded end-to-end run (bench/e2e_fixture1.json) meets the release bar: a fresh plugin
load built video + page on fixture 1, every spoken line has a blind frame-review verdict with no
contradiction, quality >= 90, a cast voice with written rejections, divergence recorded, zero frames
left, a READY manifest. This validates the shipped record; it does not re-run the model."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite  # noqa: E402


def main() -> int:
    s = Suite("e2e_record")
    p = ROOT / "bench" / "e2e_fixture1.json"
    s.check("the end-to-end record ships with the package", p.exists(), str(p))
    if not p.exists():
        return s.finish()
    r = json.loads(p.read_text(encoding="utf-8"))
    d = set(r.get("deliverables", []))
    s.check("e2e built both deliverables (mp4 + html) with captions and the claims sidecar",
            {"logtally.mp4", "logtally.html", "logtally.srt", "logtally.claims.md"} <= d, sorted(d))
    fr = r.get("frame_review", {})
    s.check("e2e: every narration line has a blind frame-review verdict",
            r.get("narration_lines", 0) > 0 and fr.get("lines_with_verdict") == r.get("narration_lines"), fr)
    s.check("e2e: no frame contradicts its sentence", fr.get("counts", {}).get("contradicts", 0) == 0, fr)
    s.check("e2e: quality build score is at least 90", (r.get("quality_build_score") or 0) >= 90,
            r.get("quality_build_score"))
    v = r.get("voice", {})
    s.check("e2e: a local voice was cast with at least two written rejections",
            v.get("id", "").split(":")[0] in ("kokoro", "piper", "say") and v.get("rejected", 0) >= 2
            and all(v.get("reasons") or []), v)
    s.check("e2e: divergence.json recorded at least one pivotal decision", r.get("divergence_decisions", 0) >= 1, "")
    s.check("e2e: zero frames left on disk", r.get("frames_left") == 0, r.get("frames_left"))
    m = r.get("manifest", {})
    s.check("e2e: verification manifest items all pass", m.get("items") and m.get("items") == m.get("pass"), m)
    g = r.get("gates", {})
    hard = ["palette", "diagram_plan", "diagram_budget", "freedom", "terms", "language:narration", "claims:video",
            "semantics", "invariants", "subtitle_check", "beat_sync", "media_probe", "disk"]
    s.check("e2e: every outcome gate that judges the build passes (palette, diagrams, freedom, terms, narration, claims, semantics, invariants, sync, media, disk)",
            all(g.get(k) == "PASS" for k in hard), {k: g.get(k) for k in hard})
    s.check("e2e: no gate errored or failed", not [k for k, x in g.items() if x in ("FAIL", "ERROR")], g)
    return s.finish()


if __name__ == "__main__":
    sys.exit(main())
