"""doctor extension: freshness of the local reference copies (read-only).

probe() -> {"summary": str, "lines": [str], "fresh": [...], "stale": [...], ...}
Never raises and never writes: any problem is reported in the summary.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

CHECK = Path(__file__).resolve().parents[1] / "refs" / "check.py"


def probe() -> dict:
    try:
        spec = importlib.util.spec_from_file_location("c8_doctor_refs_check", CHECK)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rep = mod.evaluate()
    except Exception as e:  # noqa: BLE001 — a doctor extension must never break doctor
        return {"summary": f"refs: check could not run ({type(e).__name__}: {e})",
                "lines": [], "fresh": [], "stale": [], "ok": False}
    try:
        n_f, n_s = len(rep["fresh"]), len(rep["stale"])
        days = sorted(r["fetched_at"][:10] for r in rep["refs"] if r.get("fetched_at"))
        as_of = f", oldest copy as of {days[0]}" if days else ""
        if n_s == 0:
            summary = f"refs: {n_f} fresh{as_of}"
        else:
            summary = (f"refs: {n_f} fresh, {n_s} stale ({', '.join(rep['stale'])}){as_of}"
                       " — run `coordn8 refs` to refresh")
        lines = [f"{r['status']:<8} {r['stamp']}" for r in rep["refs"]]
        lines += [f"upstream newer: {u['lib']} pinned {u['pinned']} -> latest {u['latest']}"
                  for u in rep.get("upstream_newer", [])]
        return {"summary": summary, "lines": lines, "fresh": rep["fresh"],
                "stale": rep["stale"], "refresh_failed": rep.get("refresh_failed", []),
                "upstream_newer": rep.get("upstream_newer", []),
                "refs_dir": rep.get("refs_dir"), "ok": n_s == 0}
    except Exception as e:  # noqa: BLE001
        return {"summary": f"refs: report malformed ({type(e).__name__}: {e})",
                "lines": [], "fresh": [], "stale": [], "ok": False}
