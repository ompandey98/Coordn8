#!/usr/bin/env python3
"""
Coordn8 gate — DIAGRAM PLAN (B1: "no figure is drawn without a plan entry"). Advisory.

Every figure that is DRAWN must have an entry in <build>/diagram_plan.json that says what it is
and why: a type (any non-empty name) or an invented form with a reason, at least 2 rejected
options each with a reason, and node/edge counts. A drawn figure with no entry -> REVIEW.

Drawn figures are found from:
  - `data-figure="<id>"` attributes in HTML / SVG files, and
  - page or scene manifests (JSON) that name figures: {"figure": "id"}, {"figures": ["id", {"id": ..}]}
    anywhere in the document.

Freedom (H1): a type is judged only on whether it is stated, never on whether it is listed in any
catalog. An invented form passes when it carries a reason.

Usage:
  python3 scripts/gates/check_diagram_plan.py <build-dir | diagram_plan.json> [drawn sources...]
  With no drawn sources, the build directory is scanned for *.html / *.svg and *manifest*.json;
  if nothing drawn is found, every plan entry is checked on its own.
Exit: 0 PASS · 1 REVIEW · 2 SKIPPED (no plan and nothing drawn) · 3 ERROR (unreadable plan)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

DATA_FIGURE = re.compile(r"""data-figure\s*=\s*["']([^"']+)["']""")
FIGURE_KEYS = ("figure", "figure_id", "data-figure")


def drawn_in_markup(text: str) -> list:
    return [m.strip() for m in DATA_FIGURE.findall(text) if m.strip()]


def drawn_in_manifest(doc) -> list:
    out = []

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in FIGURE_KEYS and isinstance(v, str) and v.strip():
                    out.append(v.strip())
                elif k == "figures" and isinstance(v, list):
                    for e in v:
                        if isinstance(e, str) and e.strip():
                            out.append(e.strip())
                        elif isinstance(e, dict):
                            if isinstance(e.get("id"), str) and e["id"].strip():
                                out.append(e["id"].strip())
                            walk(e)
                else:
                    walk(v)
        elif isinstance(x, list):
            for e in x:
                walk(e)

    walk(doc)
    return out


def collect_drawn(sources: list) -> tuple:
    drawn, where, unreadable = [], {}, []
    for s in sources:
        p = Path(s)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unreadable.append(str(p))
            continue
        if p.suffix.lower() == ".json":
            try:
                ids = drawn_in_manifest(json.loads(text))
            except ValueError:
                unreadable.append(str(p))
                continue
        else:
            ids = drawn_in_markup(text)
        for i in ids:
            if i not in where:
                drawn.append(i)
                where[i] = []
            where[i].append(p.name)
    return drawn, where, unreadable


def entry_problems(e: dict) -> list:
    probs = []
    inv = e.get("invented")
    typ = e.get("type")
    has_type = isinstance(typ, str) and typ.strip() != ""
    if isinstance(inv, dict) and inv:
        if not str(inv.get("name") or typ or "").strip():
            probs.append("invented form has no name")
        if not str(inv.get("why") or "").strip():
            probs.append("invented form has no reason (why)")
    elif isinstance(inv, str) and inv.strip():
        if not str(e.get("invented_why") or e.get("why") or "").strip():
            probs.append("invented form has no reason (why)")
    elif not has_type:
        probs.append("no type and no invented form")
    rej = e.get("rejected")
    good = [r for r in rej if isinstance(r, dict) and str(r.get("type") or r.get("option") or "").strip()
            and str(r.get("why") or "").strip()] if isinstance(rej, list) else []
    if len(good) < 2:
        probs.append(f"needs >= 2 rejected options with reasons (has {len(good)})")
    for k in ("nodes", "edges"):
        v = e.get(k)
        if not (isinstance(v, int) and not isinstance(v, bool) and v >= 0):
            probs.append(f"missing {k} count")
    return probs


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return EXIT_ERROR
    first = Path(argv[0])
    build = first if first.is_dir() else first.parent
    plan_path = first / "diagram_plan.json" if first.is_dir() else first
    sources = [a for a in argv[1:] if not a.startswith("--")]
    if not sources and build.is_dir():
        sources = sorted(str(p) for p in build.rglob("*") if p.is_file() and (
            p.suffix.lower() in (".html", ".htm", ".svg")
            or (p.suffix.lower() == ".json" and "manifest" in p.name.lower())))
    drawn, where, unreadable = collect_drawn(sources)

    plan = None
    if plan_path.exists():
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            emit({"gate": "diagram_plan", "status": "ERROR", "summary": f"cannot read {plan_path}: {e}"})
            return EXIT_ERROR
    if plan is None and not drawn:
        emit({"gate": "diagram_plan", "status": "SKIPPED",
              "summary": f"no plan at {plan_path} and no drawn figures found"})
        return EXIT_SKIPPED
    entries = {}
    for e in (plan or {}).get("figures", []) if isinstance(plan, dict) else []:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            entries[e["id"]] = e

    findings = []
    checked = drawn if drawn else sorted(entries)
    for fid in checked:
        e = entries.get(fid)
        if e is None:
            findings.append({"figure": fid, "drawn_in": where.get(fid, []),
                             "problems": ["drawn but has no plan entry — plan it before drawing it"]})
            continue
        probs = entry_problems(e)
        if probs:
            findings.append({"figure": fid, "drawn_in": where.get(fid, []), "problems": probs})
    planned_not_drawn = sorted(set(entries) - set(drawn)) if drawn else []
    for u in unreadable:
        findings.append({"figure": None, "source": u, "problems": ["drawn-figure source could not be read"]})
    emit({"gate": "diagram_plan", "status": "REVIEW" if findings else "PASS",
          "summary": (f"{len(findings)} figure(s) lack a complete plan entry" if findings
                      else f"{len(checked)} figure(s) planned with type/invention, rejections and counts"),
          "measures": "that each drawn figure has a plan entry with a stated form, >=2 reasoned rejections "
                      "and counts; it does not judge whether the choice was good",
          "drawn": drawn, "findings": findings, "planned_not_drawn": planned_not_drawn})
    return EXIT_REVIEW if findings else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
