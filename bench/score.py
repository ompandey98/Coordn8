#!/usr/bin/env python3
"""Coordn8 benchmark scorer — turns the raw results of bench/run.py into bench/report.md.

    python3 bench/score.py results.json [--out bench/report.md]

Deterministic: the same results give the same bytes. Every value that depends on the speed of
the machine (frames per second, wall time) is printed in ONE column, `Timing`, and nowhere else,
so two runs on the same inputs differ only inside that column. Every cell carries its tag
(`measured`, `not-available`, `skipped`, `error`); a capability a version does not have is shown
as not-available, never as a zero. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FIELDS = ("audience", "quality", "palette", "diagram", "language", "claims", "a11y",
          "fps", "peak_disk", "frames_left", "wall_s")
TIMING_FIELDS = ("fps", "wall_s")
TAGS = ("measured", "not-available", "skipped", "error")
VERSIONS = (("v1.5", "1.5.0"), ("v2", "2.0"))
COLUMNS = ("Fixture", "Version", "Quality (6 dims)", "Palette gate", "Diagram plans", "Language pass",
           "Claims coverage", "Page a11y", "Peak disk", "Frames left", "Timing")
SHORT_TAG = {"measured": "measured", "not-available": "not-available", "skipped": "skipped", "error": "error"}


def human(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def _val(cell):
    return (cell or {}).get("value") if isinstance(cell, dict) else None


def _tag(cell) -> str:
    return (cell or {}).get("tag", "error") if isinstance(cell, dict) else "error"


def fmt_cell(field: str, cell) -> str:
    """One table cell: the value in words, then its tag in brackets."""
    t = _tag(cell)
    v = _val(cell)
    if t != "measured" or v is None:
        word = {"not-available": "not available", "skipped": "skipped", "error": "error"}.get(t, t)
        return f"{word} [{SHORT_TAG.get(t, t)}]"
    if field == "quality":
        s = f"{v.get('build_score')}"
    elif field == "palette":
        s = f"{v.get('status')} · {v.get('family')} / {v.get('kit')}"
        if v.get("page_status"):
            s += f" · page {v.get('page_status')}"
    elif field == "diagram":
        s = (f"{v.get('with_2_rejections')}/{v.get('figures')} with 2+ rejections · plan {v.get('plan_gate')}"
             f" · budget {v.get('budget_gate')}")
    elif field == "language":
        s = f"{v.get('pass_rate')}% of {v.get('sentences')}"
    elif field == "claims":
        s = f"{v.get('coverage_pct')}% of {v.get('claims')}"
        if v.get("page_ledger"):
            s += f" · ledger {v.get('page_ledger')}"
    elif field == "a11y":
        s = f"{v.get('passed')}/{v.get('checks')} · DOM {v.get('dom_smoke')}"
    elif field == "peak_disk":
        s = human(v)
    elif field == "frames_left":
        s = str(v)
    elif field == "audience":
        s = f"{v.get('role')} / {v.get('level')}"
    else:
        s = str(v)
    return f"{s} [measured]"


def fmt_timing(rv: dict) -> str:
    fps, wall = rv.get("fps"), rv.get("wall_s")
    f = f"{_val(fps)} fps" if _tag(fps) == "measured" and _val(fps) is not None else f"fps {_tag(fps)}"
    w = f"{_val(wall)} s" if _tag(wall) == "measured" and _val(wall) is not None else f"wall {_tag(wall)}"
    return f"{f} · {w}"


def row_cells(name: str, label: str, rv: dict) -> list:
    return [name, label] + [fmt_cell(f, rv.get(f)) for f in
                            ("quality", "palette", "diagram", "language", "claims", "a11y",
                             "peak_disk", "frames_left")] + [fmt_timing(rv)]


# ------------------------------------------------------------------ totals
def _mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(sum(xs) / len(xs), 1) if xs else None


def totals(results: dict, key: str) -> dict:
    rows = [f.get(key) for f in results.get("fixtures") or [] if isinstance(f.get(key), dict)]
    out: dict = {"n": len(rows)}
    if not rows:
        return out

    def measured(field):
        return [_val(r.get(field)) for r in rows if _tag(r.get(field)) == "measured" and _val(r.get(field)) is not None]

    q = measured("quality")
    out["quality"] = _mean([x.get("build_score") for x in q]) if q else None
    p = measured("palette")
    out["palette"] = f"{sum(1 for x in p if x.get('status') == 'PASS')}/{len(rows)} PASS" if p else None
    dg = measured("diagram")
    out["diagram"] = (f"{sum(1 for x in dg if x.get('plan_gate') == 'PASS' and x.get('budget_gate') == 'PASS' and x.get('with_2_rejections') == x.get('figures'))}"
                      f"/{len(rows)} plans held") if dg else None
    lg = measured("language")
    out["language"] = _mean([x.get("pass_rate") for x in lg]) if lg else None
    cl = measured("claims")
    out["claims"] = _mean([x.get("coverage_pct") for x in cl]) if cl else None
    a = measured("a11y")
    out["a11y"] = f"{sum(x.get('passed') or 0 for x in a)}/{sum(x.get('checks') or 0 for x in a)}" if a else None
    pd = measured("peak_disk")
    out["peak_disk"] = max(pd) if pd else None
    fl = measured("frames_left")
    out["frames_left"] = sum(fl) if fl else None
    fp = measured("fps")
    out["fps"] = _mean(fp) if fp else None
    ws = measured("wall_s")
    out["wall_s"] = round(sum(ws), 2) if ws else None
    out["measured_fields"] = sum(1 for r in rows for f in FIELDS if _tag(r.get(f)) == "measured")
    out["all_fields"] = len(rows) * len(FIELDS)
    return out


def _t(v, suffix: str = "") -> str:
    return "n/a" if v is None else f"{v}{suffix}"


def totals_line(results: dict) -> str:
    a, b = totals(results, "v1.5"), totals(results, "v2")
    parts = []
    for name, key, suf in (("quality", "quality", ""), ("palette", "palette", ""), ("diagram", "diagram", ""),
                           ("language", "language", "%"), ("claims", "claims", "%"), ("a11y", "a11y", ""),
                           ("frames left", "frames_left", "")):
        parts.append(f"{name} {_t(a.get(key), suf)} -> {_t(b.get(key), suf)}")
    parts.append(f"peak disk {human(a['peak_disk']) if a.get('peak_disk') else 'n/a'} -> "
                 f"{human(b['peak_disk']) if b.get('peak_disk') else 'n/a'}")
    parts.append(f"measured fields {a.get('measured_fields', 0)}/{a.get('all_fields', 0)} -> "
                 f"{b.get('measured_fields', 0)}/{b.get('all_fields', 0)}")
    timing = (f" | timing: fps {_t(a.get('fps'))} -> {_t(b.get('fps'))}, wall {_t(a.get('wall_s'), ' s')} -> "
              f"{_t(b.get('wall_s'), ' s')}")
    return f"TOTALS {len(results.get('fixtures') or [])} fixture(s), 1.5.0 -> 2.0: " + " · ".join(parts) + timing


# ------------------------------------------------------------------ report
def _table(header, rows) -> list:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return out


def render_report(results: dict) -> str:
    L = []
    L.append("# Coordn8 benchmark — 1.5.0 vs 2.0")
    L.append("")
    L.append("Generated by `python3 bench/run.py` (scored by `bench/score.py`). Six fixed fixtures in "
             "`bench/fixtures/`, each with the brief a user would type in `bench/briefs/`. One deterministic "
             "toolchain pass per fixture and version; no model is invoked and nothing is sent to any hosted service.")
    L.append("")
    L.append(f"- Package under test: {results.get('package_version')} · baseline: {results.get('baseline_version')} "
             f"· render: {results.get('render')} at {results.get('fps')} fps")
    L.append("- Tags: `measured` = run and read here · `not-available` = the capability does not exist in that "
             "version (never shown as a zero) · `skipped` = not requested or a tool is missing here · `error` = the "
             "step ran and failed.")
    L.append("- Every speed-dependent number is in the **Timing** column only; all other cells are the same "
             "for the same inputs.")
    L.append("- A gate passing means the property it measures holds. It is not a judgement that the page or video "
             "is good: no frame-level review is part of this benchmark.")
    L.append("")
    L.append("## Totals")
    L.append("")
    L.append("`" + totals_line(results).split(" | timing:")[0] + "`")
    L.append("")
    rows = []
    for key, label in VERSIONS:
        t = totals(results, key)
        if not t.get("n"):
            continue
        rows.append(["all", label, _t(t.get("quality")), _t(t.get("palette")), _t(t.get("diagram")),
                     _t(t.get("language"), "%"), _t(t.get("claims"), "%"), _t(t.get("a11y")),
                     human(t["peak_disk"]) if t.get("peak_disk") else "n/a", _t(t.get("frames_left")),
                     f"{_t(t.get('fps'))} fps · {_t(t.get('wall_s'), ' s')}"])
    L += _table(COLUMNS, rows)
    L.append("")
    L.append("## Per fixture")
    L.append("")
    rows = []
    for fx in results.get("fixtures") or []:
        for key, label in VERSIONS:
            if isinstance(fx.get(key), dict):
                rows.append(row_cells(fx["fixture"], label, fx[key]))
    L += _table(COLUMNS, rows)
    L.append("")
    L.append("### Fixtures")
    L.append("")
    rows = [[fx["fixture"], fx.get("project"), fx.get("brief"), fx.get("files"), human(fx.get("bytes"))]
            for fx in results.get("fixtures") or []]
    L += _table(("Fixture", "Project", "Brief", "Files", "Size"), rows)
    L.append("")
    L.append("### Detail (2.0)")
    L.append("")
    rows = []
    for fx in results.get("fixtures") or []:
        v = fx.get("v2") or {}
        q, d, la = _val(v.get("quality")) or {}, _val(v.get("diagram")) or {}, _val(v.get("language")) or {}
        dims = q.get("dimensions") or {}
        lay = d.get("layout") or {}
        rows.append([fx["fixture"], _t((_val(v.get("audience")) or {}).get("role")),
                     " ".join(f"{k[:5]} {dims[k]}" for k in sorted(dims)) or "n/a",
                     _t(q.get("findings")), f"{_t(d.get('type'))} ({_t(lay.get('rankdir'))}, x{_t(lay.get('scale'))})",
                     _t(la.get("sentences_with_findings")),
                     ", ".join((_val(v.get("a11y")) or {}).get("failed") or []) or "none"])
    L += _table(("Fixture", "Audience", "Quality by dimension", "Quality findings", "Diagram (layout)",
                 "Sentences with findings", "A11y checks to review"), rows)
    L.append("")
    notes = []
    for fx in results.get("fixtures") or []:
        for key, label in VERSIONS:
            for f in FIELDS:
                c = (fx.get(key) or {}).get(f)
                if isinstance(c, dict) and c.get("note"):
                    notes.append(f"- {fx['fixture']} · {label} · {f}: {c['note']}")
    if notes:
        L.append("### Notes")
        L.append("")
        seen, uniq = set(), []
        for n in notes:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        L += uniq
        L.append("")
    cov = results.get("coverage") or {}
    L.append("## Capability-coverage matrix")
    L.append("")
    if cov.get("available"):
        tot = cov.get("totals") or {}
        L.append(f"`tests/coverage_matrix.json`: {cov.get('rows')} rows — "
                 + ", ".join(f"{k} {tot[k]}" for k in sorted(tot)) + ".")
        L.append("")
        verdicts = sorted({v for g in cov["by_group"].values() for v in g})
        rows = [[g] + [cov["by_group"][g].get(v, 0) for v in verdicts] for g in sorted(cov["by_group"])]
        L += _table(["Group"] + verdicts, rows)
    else:
        L.append("The coverage matrix was not readable in this run.")
    L.append("")
    L.append("## Head-to-head with the references")
    L.append("")
    L.append("Each reference was checked for a run on a fixture that is local, uses no hosted AI and does not run "
             "its code against the real home directory.")
    L.append("")
    rows = [[r["ref"], r["kind"], "yes" if r["runs_on_fixture"] else "no", r["why"]]
            for r in results.get("references") or []]
    L += _table(("Reference", "What it is", "Runs on a fixture here", "Why"), rows)
    L.append("")
    ca = results.get("crosscheck_a")
    if ca is not None:
        if ca.get("ran"):
            L.append(f"Cross-check that did run: reference A's rendered-page linter on the 2.0 page for fixture 01 "
                     f"-> exit {ca.get('exit')} ({ca.get('verdict')}). Last line: `{ca.get('last_line')}`")
        else:
            L.append(f"Cross-check with reference A: not run — {ca.get('why')}.")
        L.append("")
    L.append("## What this benchmark does not show")
    L.append("")
    L.append("- Whether the page or video explains the system well: that needs a human or a blind frame-by-frame "
             "review, which is not part of this run.")
    L.append("- 1.5.0 output quality on a fixture: 1.5.0 had no palette, kit, diagram planner, layout engine or page "
             "builder, so it cannot compose the page or the scenes from a fixture without a model. Its own tools "
             "that do exist (quality scorer, claims gate, frame capture with the disk guard) were run on the same inputs.")
    L.append("- Narration timing comes from a fixed words-per-second rate, not a voice; voice sync is measured "
             "against those timings.")
    L.append("- Frames per second and wall time depend on the machine and its load at the time of the run.")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="render bench/report.md from bench/run.py results")
    ap.add_argument("results")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    rep = render_report(res)
    if a.out:
        Path(a.out).write_text(rep, encoding="utf-8")
    else:
        sys.stdout.write(rep)
    sys.stderr.write(totals_line(res) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
