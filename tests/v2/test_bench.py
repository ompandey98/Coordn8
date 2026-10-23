#!/usr/bin/env python3
"""Suite "bench" (G1): the benchmark fixtures, runner and scorer.

Runs bench/run.py three times on fixture 01 (twice without a render, once with the short render
on both versions) in a temporary HOME, and checks the scorer offline. Nothing is written into
the package: every report goes to a temporary path.
"""
from __future__ import annotations

import copy
import json
import os
import re
import site
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "bench"))
import run as bench_run  # noqa: E402
import score  # noqa: E402
import test_name_leak as leak  # noqa: E402

s = Suite("bench")
NAMES = ("01-cli", "02-web-api", "03-data-pipeline", "04-ml-training", "05-infra-as-code", "06-docs-only")


def snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


# ---------------------------------------------------------------- fixtures and briefs
dirs = bench_run.fixture_dirs()
s.check("six fixtures exist, one per kind (cli, web api, data pipeline, ml training, infra as code, docs only)",
        [d.name for d in dirs] == list(NAMES), [d.name for d in dirs])
for d in dirs:
    st = bench_run.fixture_stats(d)
    s.check(f"fixture {d.name} is within size limits (<= 40 files, <= 200 KB)",
            st["files"] <= bench_run.FIXTURE_MAX_FILES and st["bytes"] <= bench_run.FIXTURE_MAX_BYTES, st)
    try:
        spec = bench_run.load_fixture(d)
        brief = spec["_brief"]
        ok_brief = all(k in brief for k in ("Deliverable", "Focus")) and len(brief.split()) >= 25
    except Exception as e:  # noqa: BLE001
        spec, ok_brief = None, False
        brief = str(e)
    s.check(f"fixture {d.name} has a brief that states the audience, the deliverable and the focus", ok_brief,
            brief[:200])
    if spec:
        claims = bench_run.claims_for(spec)
        unresolved = [c["id"] for c in claims if not c.get("ref") and not c.get("inferred")]
        s.check(f"fixture {d.name}: every flow step and fact anchors to a real path:line in the fixture",
                not unresolved, unresolved)
        code_files = [p for p in d.rglob("*") if p.is_file() and p.name != "BENCH.json"]
        s.check(f"fixture {d.name} is a real repository (>= 6 files besides the bench spec)", len(code_files) >= 6,
                len(code_files))
with tmpdir() as td:
    big = Path(td) / "99-big"
    big.mkdir()
    for i in range(41):
        (big / f"f{i}.txt").write_text("x" * 6000)
    st = bench_run.fixture_stats(big)
    s.check("negative: the size check catches an oversized fixture (41 files, 246 KB)",
            st["files"] > bench_run.FIXTURE_MAX_FILES and st["bytes"] > bench_run.FIXTURE_MAX_BYTES, st)

# ---------------------------------------------------------------- the runner
real_env = {}
pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(Path.home() / "Library" / "Caches" / "ms-playwright")
if Path(pw).exists():
    real_env["PLAYWRIGHT_BROWSERS_PATH"] = pw
if getattr(site, "USER_BASE", None) and Path(site.USER_BASE).exists():
    real_env["PYTHONUSERBASE"] = site.USER_BASE

pkg_before = snapshot(ROOT / "bench")
results = {}
with tmpdir() as td:
    tdp = Path(td)
    env = sandbox_env(tdp)
    env.update(real_env)
    env["TMPDIR"] = str(tdp / "tmp")
    (tdp / "tmp").mkdir()
    for label, args in (("none", ["--fixture", "1", "--version", "v2", "--render", "none"]),
                        ("none2", ["--fixture", "1", "--version", "v2", "--render", "none"]),
                        ("short", ["--fixture", "1", "--version", "both", "--render", "short"])):
        t0 = time.monotonic()
        cp = run("bench/run.py", args + ["--report", tdp / f"report_{label}.md", "--results-out",
                                         tdp / f"results_{label}.json"], env=env, timeout=400)
        dt = time.monotonic() - t0
        res = (json.loads((tdp / f"results_{label}.json").read_text()) if (tdp / f"results_{label}.json").exists()
               else {})
        rep = (tdp / f"report_{label}.md").read_text() if (tdp / f"report_{label}.md").exists() else ""
        results[label] = {"rc": cp.returncode, "dt": dt, "res": res, "rep": rep, "err": cp.stderr[-600:]}
    leftover = [p for p in (tdp / "tmp").rglob("*")]
    home_files = [p for p in (tdp / "home").rglob("*") if p.is_file()]
# The macOS system Python writes its own bytecode cache under ~/Library/Caches/com.apple.python
# whenever HOME changes; that is the interpreter, not the bench, so it is not counted.
home_files = [p for p in home_files if "Library/Caches/com.apple.python" not in str(p)]

r1 = results["none"]
s.check("run.py --fixture 1 --render none completes in under 120 s", r1["rc"] == 0 and r1["dt"] < 120,
        f"rc={r1['rc']} dt={r1['dt']:.1f}s {r1['err']}")
row = ((r1["res"].get("fixtures") or [{}])[0]).get("v2") or {}
missing = [f for f in score.FIELDS if f not in row]
untagged = [f for f in score.FIELDS if f in row and row[f].get("tag") not in score.TAGS]
s.check("the report row carries every score field (quality, palette, diagram, language, claims, a11y, fps, "
        "peak disk, frames left, wall time)", not missing, missing)
s.check("every score field is tagged measured / not-available / skipped / error", not untagged and not missing,
        untagged)
for f in ("quality", "palette", "diagram", "language", "claims", "a11y"):
    s.check(f"2.0 on fixture 01: the {f} score is measured", (row.get(f) or {}).get("tag") == "measured",
            row.get(f))
q = ((row.get("quality") or {}).get("value") or {})
s.check("2.0 on fixture 01: all six quality dimensions are scored",
        sorted((q.get("dimensions") or {})) == sorted(("alignment", "overlap", "voice_sync", "density",
                                                       "legibility", "motion")), q)
s.check("--render none marks frames/s and peak disk as skipped, never as a number",
        row.get("fps", {}).get("tag") == "skipped" and row.get("peak_disk", {}).get("tag") == "skipped",
        (row.get("fps"), row.get("peak_disk")))
s.check("the report row for fixture 01 is written to the report", "| 01-cli | 2.0 |" in r1["rep"], r1["rep"][:300])

r3 = results["short"]
fx = (r3["res"].get("fixtures") or [{}])[0]
v2, v15 = fx.get("v2") or {}, fx.get("v1.5") or {}
fps = (v2.get("fps") or {})
s.check("--render short on fixture 01: frames per second > 0 (measured)",
        r3["rc"] == 0 and fps.get("tag") == "measured" and (fps.get("value") or 0) > 0, (fps, r3["err"]))
pdk = (v2.get("peak_disk") or {})
s.check("--render short: peak disk is measured by the disk guard (bytes > 0)",
        pdk.get("tag") == "measured" and (pdk.get("value") or 0) > 0, pdk)
rend = ((v2.get("render") or {}).get("value") or {})
s.check("--render short renders 2 scenes for 6-10 s in total",
        6.0 <= float(rend.get("render_seconds") or 0) <= 10.0, rend)
s.check("--render short leaves zero frames on disk (per version, whole work dir, and the work dir is removed)",
        (v2.get("frames_left") or {}).get("value") == 0 and (v15.get("frames_left") or {}).get("value") == 0
        and r3["res"].get("frames_left_total") == 0 and r3["res"].get("work_dir_removed") is True
        and not leftover, (r3["res"].get("frames_left_total"), r3["res"].get("work_dir_removed"), leftover[:5]))
s.check("no run wrote into the temporary HOME's files outside the bench work dir", not home_files,
        [str(p) for p in home_files[:5]])

s.check("the 1.5.0 run uses the extracted baseline tree (its VERSION is 1.5.0)",
        r3["res"].get("baseline_version") == "1.5.0", r3["res"].get("baseline_version"))
na_fields = ("palette", "diagram", "language", "a11y", "quality")
s.check("1.5.0: absent capabilities (palette, diagram, language, page a11y, scene composition) are "
        "not-available, never a zero",
        all((v15.get(f) or {}).get("tag") == "not-available" and (v15.get(f) or {}).get("value") is None
            for f in na_fields), {f: v15.get(f) for f in na_fields})
s.check("1.5.0: its own tools that exist (claims gate, frame capture with the disk guard) are measured",
        all((v15.get(f) or {}).get("tag") == "measured" for f in ("claims", "fps", "peak_disk")),
        {f: v15.get(f) for f in ("claims", "fps", "peak_disk")})
c2 = ((v2.get("claims") or {}).get("value") or {})
c15 = ((v15.get("claims") or {}).get("value") or {})
s.check("claims coverage: 2.0 counts the honest inferred mark, 1.5.0 cannot (measured difference)",
        c2.get("coverage_pct") == 100.0 and (c15.get("coverage_pct") or 100) < 100.0
        and c2.get("inferred_mark_understood") is True and c15.get("inferred_mark_understood") is False,
        (c2, c15))
s.check("1.5.0's quality scorer gives the identical score on the same trace (scorer parity recorded)",
        "scorer parity on the same trace: identical" in ((v15.get("quality") or {}).get("note") or ""),
        v15.get("quality"))
rep = r3["rep"]
s.check("the report shows 1.5.0 -> 2.0 per fixture and a totals row for each version",
        "| 01-cli | 1.5.0 |" in rep and "| 01-cli | 2.0 |" in rep and "| all | 1.5.0 |" in rep
        and "| all | 2.0 |" in rep and "TOTALS" in rep, rep[:400])
s.check("the report carries the coverage-matrix summary (rows better / parity)",
        "## Capability-coverage matrix" in rep and re.search(r"better \d+", rep) and re.search(r"parity \d+", rep),
        rep[rep.find("## Capability"):][:300])
s.check("the report carries the head-to-head table with references A-F and why each could or could not run",
        all(f"| {x} |" in rep for x in "ABCDEF") and "## Head-to-head" in rep, "")
row15 = next((ln for ln in rep.splitlines() if ln.startswith("| 01-cli | 1.5.0 |")), "")
cells = [c.strip() for c in row15.strip("|").split("|")]
s.check("negative: no not-available cell is printed as a number (1.5.0 palette / diagram / language / a11y)",
        len(cells) == len(score.COLUMNS) and all("not available [not-available]" == cells[i] for i in (2, 3, 4, 5, 7)),
        cells)

# ---------------------------------------------------------------- names and determinism
with tmpdir() as td:
    tdp = Path(td)
    (tdp / "report.md").write_text(rep, encoding="utf-8")
    (tdp / "bench_files").mkdir()
    hits = leak.scan(tdp) + leak.scan(ROOT / "bench")
    s.check("name-leak: the generated report and every bench file contain no reference names", not hits, hits[:5])
    (tdp / "seeded.md").write_text(rep + "\nsee " + "weil" + "anx for details\n", encoding="utf-8")
    s.check("negative: the name scan catches a reference name seeded into a report", bool(leak.scan(tdp)), "")

a, b = results["none"]["res"], results["none2"]["res"]


def no_timing(rep_text: str) -> str:
    out = []
    for ln in rep_text.splitlines():
        if ln.startswith("| ") and ln.count("|") == len(score.COLUMNS) + 1:
            ln = "|".join(ln.split("|")[:-2]) + "|"
        out.append(ln)
    return "\n".join(out)


s.check("score.py is deterministic: the same results give byte-identical reports",
        bool(a) and score.render_report(a) == score.render_report(copy.deepcopy(a)), "")
s.check("two runs on the same inputs give reports that differ only in the Timing column",
        bool(a) and bool(b) and no_timing(score.render_report(a)) == no_timing(score.render_report(b)),
        [x for x in zip(no_timing(score.render_report(a)).splitlines(),
                        no_timing(score.render_report(b)).splitlines()) if x[0] != x[1]][:3])
m = copy.deepcopy(a)
m["fixtures"][0]["v2"]["wall_s"]["value"] = 999.99
m["wall_s_total"] = 1234.5
ra, rm = score.render_report(a), score.render_report(m)
s.check("timing fields are isolated in one column: changing wall time changes only Timing cells",
        ra != rm and no_timing(ra) == no_timing(rm), "")
m2 = copy.deepcopy(a)
m2["fixtures"][0]["v2"]["claims"]["value"]["coverage_pct"] = 50.0
s.check("negative: changing a non-timing score changes the report outside the Timing column",
        no_timing(score.render_report(m2)) != no_timing(ra), "")
s.check("score.py renders a not-available cell as text with its tag, never as 0",
        score.fmt_cell("palette", bench_run.na("absent")) == "not available [not-available]", "")
s.check("running the bench wrote nothing into the package (reports went to the given path)",
        snapshot(ROOT / "bench") == pkg_before, "")

sys.exit(s.finish())
