#!/usr/bin/env python3
"""Invention ledger (H5): every invented palette, kit, diagram form, scene, caption style and page
component is recorded with where it was used and how it fared at the outcome gates; one that
passes in two different builds is offered for promotion into the user's LOCAL catalog.

    inventions.py record  <build_dir> [--build-id ID] [--gate-results FILE ...] [--gate NAME=STATUS ...]
    inventions.py list    [--kind K]
    inventions.py offers
    inventions.py promote <kind> <name> [--force]

Where things live (user zone, never the package):
    <COORDN8_HOME or ~/.coordn8>/library/inventions.jsonl     append-only event log
    <COORDN8_HOME or ~/.coordn8>/library/catalog/<kind>.json  the local catalog overlay

What `record` finds in a build directory (searched recursively):
    design_system.json   palette whose family is "invented:…" or that says invented: true;
                         kit "invented:…" (with its kit_spec); caption_style marked invented
    diagram_plan.json    every figure with an `invented` form
    trace JSON           every scene form the runtime recorded as kind "invented" (declareForm)
    page content JSON    every section with a custom layout (custom_html / custom_kind)
    inventions.json      anything the agent declared directly: {"inventions": [{kind, name, reason, definition}]}

Gate results come from --gate-results files (a gate's JSON line, a list of them, {"gates": {name:
status}} or {name: status}), from --gate NAME=STATUS, and from <build>/gate_results.json. Each
invention is judged only by the gates that apply to its kind (palette → palette; diagram →
diagram plan / budget / connectors; scene → semantics / invariants / density / determinism; …);
when none of those were run, every given gate applies. A use PASSES when at least one applicable
gate passed and none returned REVIEW / FAIL / ERROR (SKIPPED is neither). An invention with no
recorded reason never passes — the doctrine is "invent, and say why".

The promotion offer is printed and recorded exactly once per invention: when its passing uses
span two different builds (a build is identified by --build-id, else by its resolved path). The
same invention under a new name is recognised by the fingerprint of its definition. `promote`
copies the latest passing definition into the overlay — the previous overlay is backed up under
library/catalog/.backups/, only that entry's key changes, the write is atomic — and it never
touches the package's data/ directory or anything outside the library folder.

Exit codes: 0 ok · 1 nothing to promote / refused · 2 nothing found · 3 error. Python 3.9+, no deps.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.paths import PKG_ROOT, user_home  # noqa: E402

try:
    import fcntl  # type: ignore
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore

KINDS = ("palette", "kit", "diagram", "scene", "caption_style", "page_component")
APPLIES = {
    "palette": ("palette",),
    "kit": ("palette", "page_a11y", "kit", "determinism"),
    "diagram": ("diagram_plan", "diagram_budget", "connectors", "layout"),
    "scene": ("semantics", "invariants", "density", "determinism", "continuity"),
    "caption_style": ("caption",),
    "page_component": ("page_a11y", "visual_density", "palette", "page_content"),
}
PASS_WORDS = {"PASS"}
FAIL_STATUSES = {"REVIEW", "FAIL", "ERROR", "BLOCK", "BLOCKED"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "frames", "vendor"}
VOLATILE = {"id", "name", "family", "reason", "why", "invented", "note", "kit", "palette_family"}


# ------------------------------------------------------------------ store
def library_dir() -> Path:
    return user_home() / "library"


def ledger_path() -> Path:
    return library_dir() / "inventions.jsonl"


def overlay_path(kind: str) -> Path:
    return library_dir() / "catalog" / f"{kind}.json"


@contextlib.contextmanager
def _locked():
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(str(p) + ".lock", "a+")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def read_events() -> List[dict]:
    p = ledger_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # a torn line is skipped, never fatal
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _append(events: Iterable[dict]) -> None:
    p = ledger_path()
    with open(p, "a", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------ identity
def norm_name(name: str) -> str:
    s = str(name or "").strip()
    s = re.sub(r"^invented\s*:\s*", "", s, flags=re.I)
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "unnamed"


def fingerprint(definition) -> str:
    def strip(x):
        if isinstance(x, dict):
            return {k: strip(v) for k, v in sorted(x.items()) if k not in VOLATILE}
        if isinstance(x, list):
            return [strip(v) for v in x]
        return x
    blob = json.dumps(strip(definition), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def build_identity(build_dir: Path, build_id: Optional[str]) -> str:
    if build_id:
        return str(build_id)
    return hashlib.sha1(str(build_dir.resolve()).encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ discovery
def _is_invented(value) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith("invented")


def _files(build_dir: Path) -> List[Path]:
    out = []
    for root, dirs, files in os.walk(build_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".json"):
                out.append(Path(root) / f)
    return sorted(out)


def _load(p: Path):
    try:
        if p.stat().st_size > 20_000_000:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _rel(p: Path, base: Path) -> str:
    try:
        return str(p.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(p)


def discover(build_dir: Path) -> List[dict]:
    """Every invention in the build: [{kind, name, reason, definition, where}]."""
    found: Dict[tuple, dict] = {}

    def add(kind, name, reason, definition, where):
        key = (kind, norm_name(name))
        if key in found:
            if where not in found[key]["where"]:
                found[key]["where"].append(where)
            return
        found[key] = {"kind": kind, "name": str(name), "reason": str(reason or "").strip(),
                      "definition": definition, "where": [where]}

    for p in _files(build_dir):
        doc = _load(p)
        if not isinstance(doc, dict):
            continue
        where = _rel(p, build_dir)
        if p.name == "inventions.json" and isinstance(doc.get("inventions"), list):
            for inv in doc["inventions"]:
                if isinstance(inv, dict) and inv.get("kind") in KINDS and inv.get("name"):
                    add(inv["kind"], inv["name"], inv.get("reason") or inv.get("why"),
                        inv.get("definition") or {}, where)
            continue
        pal = doc.get("palette")
        if isinstance(pal, dict) and isinstance(pal.get("roles"), dict) and \
                (_is_invented(pal.get("family")) or pal.get("invented") is True):
            add("palette", pal.get("family") or "invented:palette", pal.get("reason") or pal.get("why"),
                {"roles": pal.get("roles"), "dark": pal.get("dark")}, where + "#palette")
        kit = doc.get("kit")
        spec = doc.get("kit_spec") if isinstance(doc.get("kit_spec"), dict) else None
        if _is_invented(kit) or (spec and (spec.get("invented") is True or _is_invented(spec.get("id")))):
            add("kit", kit if isinstance(kit, str) else (spec or {}).get("id", "invented:kit"),
                (spec or {}).get("reason") or doc.get("kit_reason"), spec or {"id": kit}, where + "#kit")
        cap = doc.get("caption_style")
        if isinstance(cap, dict) and (cap.get("invented") is True or _is_invented(cap.get("id"))):
            add("caption_style", cap.get("id") or "invented:caption", cap.get("reason") or cap.get("why"),
                cap, where + "#caption_style")
        if isinstance(doc.get("figures"), list) and p.name.startswith("diagram_plan"):
            for fig in doc["figures"]:
                if not isinstance(fig, dict):
                    continue
                inv = fig.get("invented")
                if isinstance(inv, dict) and inv:
                    add("diagram", inv.get("name") or fig.get("type") or fig.get("id"), inv.get("why"),
                        {"invented": inv, "behaviour": fig.get("behaviour"), "nodes": fig.get("nodes"),
                         "edges": fig.get("edges"), "budget": inv.get("budget")}, f"{where}#{fig.get('id')}")
                elif isinstance(inv, str) and inv.strip():
                    add("diagram", inv, fig.get("invented_why") or fig.get("why"),
                        {"invented": inv, "behaviour": fig.get("behaviour"), "nodes": fig.get("nodes"),
                         "edges": fig.get("edges")}, f"{where}#{fig.get('id')}")
        if isinstance(doc.get("frames"), list) and isinstance(doc.get("forms"), list):
            for form in doc["forms"]:
                if isinstance(form, dict) and form.get("kind") == "invented":
                    add("scene", form.get("name"), form.get("reason"),
                        {"name": form.get("name"), "section": form.get("section"), "scene": doc.get("scene")},
                        f"{where}#{doc.get('scene')}")
        if isinstance(doc.get("sections"), list) and ("title" in doc or "conclusion" in doc):
            for sec in doc["sections"]:
                if not isinstance(sec, dict) or not (sec.get("custom_html") or _is_invented(sec.get("custom_kind"))):
                    continue
                name = sec.get("custom_kind") or f"custom:{sec.get('id')}"
                html = str(sec.get("custom_html") or "")
                add("page_component", name, sec.get("custom_explains") or sec.get("reason"),
                    {"custom_kind": sec.get("custom_kind"), "custom_explains": sec.get("custom_explains"),
                     "primary_visual": sec.get("primary_visual"), "interaction": sec.get("interaction"),
                     "custom_elements": sec.get("custom_elements"), "custom_html": html,
                     "html_sha": hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]},
                    f"{where}#{sec.get('id')}")
    return list(found.values())


# ------------------------------------------------------------------ gate results
def _gate_entries(doc) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if isinstance(doc, list):
        for d in doc:
            out.update(_gate_entries(d))
    elif isinstance(doc, dict):
        if isinstance(doc.get("gate"), str) and isinstance(doc.get("status"), str):
            out[doc["gate"]] = doc["status"].upper()
        elif isinstance(doc.get("gates"), dict):
            out.update({str(k): str(v.get("status") if isinstance(v, dict) else v).upper()
                        for k, v in doc["gates"].items()})
        elif isinstance(doc.get("gates"), list):
            out.update(_gate_entries(doc["gates"]))
        else:
            for k, v in doc.items():
                if isinstance(v, str) and v.upper() in PASS_WORDS | FAIL_STATUSES | {"SKIPPED"}:
                    out[str(k)] = v.upper()
    return out


def load_gate_results(files: Iterable[str] = (), pairs: Iterable[str] = ()) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for f in files:
        p = Path(f)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        try:
            out.update(_gate_entries(json.loads(text)))
        except ValueError:
            for line in text.splitlines():  # a log of gate JSON lines
                line = line.strip()
                if line.startswith("{"):
                    with contextlib.suppress(ValueError):
                        out.update(_gate_entries(json.loads(line)))
    for pr in pairs:
        if "=" in pr:
            k, v = pr.split("=", 1)
            out[k.strip()] = v.strip().upper()
    return out


def judge(kind: str, gates: Dict[str, str], reason: str) -> tuple:
    keys = APPLIES.get(kind, ())
    applicable = {g: s for g, s in gates.items() if any(k in g.lower() for k in keys)} or dict(gates)
    bad = {g: s for g, s in applicable.items() if s in FAIL_STATUSES}
    passed = bool(reason) and not bad and any(s in PASS_WORDS for s in applicable.values())
    why = ("no reason recorded for the invention" if not reason else
           (f"failed: {', '.join(f'{g}={s}' for g, s in sorted(bad.items()))}" if bad else
            ("passed " + ", ".join(sorted(g for g, s in applicable.items() if s in PASS_WORDS))
             if passed else "no applicable gate passed")))
    return passed, applicable, why


# ------------------------------------------------------------------ grouping and offers
def _groups(events: List[dict]) -> Dict[str, dict]:
    """key -> {kind, name, keys, fps, uses, offered, promoted}; same fingerprint joins a group."""
    groups: Dict[str, dict] = {}
    by_fp: Dict[tuple, str] = {}
    for ev in events:
        kind, key, fp = ev.get("kind"), ev.get("key"), ev.get("fingerprint")
        if not kind or not key:
            continue
        gk = key
        if key not in groups and fp and (kind, fp) in by_fp:
            gk = by_fp[(kind, fp)]
        else:
            for g, info in groups.items():
                if key in info["keys"]:
                    gk = g
                    break
        g = groups.setdefault(gk, {"kind": kind, "name": ev.get("name"), "keys": set(), "fps": set(),
                                   "uses": [], "offered": False, "promoted": False})
        g["keys"].add(key)
        if fp:
            g["fps"].add(fp)
            by_fp.setdefault((kind, fp), gk)
        e = ev.get("event")
        if e == "use":
            g["uses"].append(ev)
        elif e == "offer":
            g["offered"] = True
        elif e == "promoted":
            g["promoted"] = True
    return groups


def passing_builds(group: dict) -> List[str]:
    out = []
    for u in group["uses"]:
        if u.get("passed") and u.get("build_id") not in out:
            out.append(u.get("build_id"))
    return out


def offer_text(kind: str, name: str, builds: List[str]) -> str:
    return (f"PROMOTION OFFER: the invented {kind} '{name}' passed its outcome gates in {len(builds)} different "
            f"builds ({', '.join(builds)}). Add it to your local catalog with: "
            f"python3 scripts/design/inventions.py promote {kind} {norm_name(name)}")


# ------------------------------------------------------------------ commands
def record(build_dir, gate_results: Optional[Dict[str, str]] = None, build_id: Optional[str] = None,
           gate_files: Iterable[str] = ()) -> dict:
    build_dir = Path(build_dir)
    if not build_dir.is_dir():
        return {"status": "ERROR", "summary": f"no build directory at {build_dir}"}
    gates = load_gate_results([str(build_dir / "gate_results.json")] + list(gate_files))
    gates.update({k: str(v).upper() for k, v in (gate_results or {}).items()})
    bid = build_identity(build_dir, build_id)
    found = discover(build_dir)
    written, offers = [], []
    with _locked():
        events = read_events()
        existing = {(e.get("key"), e.get("build_id"), e.get("fingerprint"), e.get("passed"))
                    for e in events if e.get("event") == "use"}
        new_events = []
        for inv in found:
            key = f"{inv['kind']}:{norm_name(inv['name'])}"
            fp = fingerprint(inv["definition"])
            passed, applicable, why = judge(inv["kind"], gates, inv["reason"])
            ev = {"event": "use", "ts": _now(), "kind": inv["kind"], "name": inv["name"], "key": key,
                  "fingerprint": fp, "reason": inv["reason"], "build_id": bid, "build": str(build_dir.resolve()),
                  "where": inv["where"], "gates": applicable, "passed": passed, "verdict": why,
                  "definition": inv["definition"]}
            written.append({k: ev[k] for k in ("kind", "name", "key", "passed", "verdict", "where")})
            if (key, bid, fp, passed) in existing:
                written[-1]["logged"] = "already recorded"
                continue
            written[-1]["logged"] = "new"
            new_events.append(ev)
        all_events = events + new_events
        groups = _groups(all_events)
        for gk, g in groups.items():
            builds = passing_builds(g)
            if len(builds) >= 2 and not g["offered"] and any(e in new_events for e in g["uses"]):
                text = offer_text(g["kind"], g["name"], builds)
                offers.append({"kind": g["kind"], "name": g["name"], "key": gk, "builds": builds, "text": text})
                new_events.append({"event": "offer", "ts": _now(), "kind": g["kind"], "name": g["name"],
                                   "key": gk, "builds": builds, "text": text})
        if new_events:
            _append(new_events)
    for o in offers:
        print(o["text"], file=sys.stderr)
    return {"status": "PASS" if found else "SKIPPED", "build_id": bid, "ledger": str(ledger_path()),
            "inventions": written, "offers": offers, "gates_seen": gates,
            "summary": (f"{len(found)} invention(s) recorded from {build_dir}; {len(offers)} promotion offer(s)"
                        if found else f"no invented palette/kit/diagram/scene/caption/page component in {build_dir}")}


def _merge_overlay(target: Path, entry_id: str, entry: dict) -> Optional[str]:
    """Set entries[entry_id] in the overlay; every other key is kept byte-for-byte in meaning."""
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        original = json.loads(target.read_text(encoding="utf-8") or "{}")
        if not isinstance(original, dict):
            raise ValueError(f"refusing to touch {target}: top level is not an object")
        bdir = target.parent / ".backups" / time.strftime("%Y%m%d-%H%M%S")
        bdir.mkdir(parents=True, exist_ok=True)
        dest = bdir / target.name
        n = 1
        while dest.exists():
            dest = bdir / f"{target.name}.{n}"
            n += 1
        dest.write_bytes(target.read_bytes())
        backup = str(dest)
    else:
        original = {"schema": 1, "about": "Local catalog overlay: inventions this user promoted after they passed "
                                          "the outcome gates in two builds. A starting point, never a fence.",
                    "entries": {}}
    data = json.loads(json.dumps(original))
    data.setdefault("entries", {})
    if not isinstance(data["entries"], dict):
        raise ValueError(f"refusing to touch {target}: 'entries' is not an object")
    data["entries"][entry_id] = entry
    check = json.loads(json.dumps(data))
    check["entries"].pop(entry_id, None)
    before = json.loads(json.dumps(original))
    before.setdefault("entries", {}).pop(entry_id, None)
    if check != before:
        raise ValueError("internal check failed: keys other than the promoted entry would change")
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return backup


def listing(kind: Optional[str] = None) -> List[dict]:
    out = []
    for gk, g in _groups(read_events()).items():
        if kind and g["kind"] != kind:
            continue
        out.append({"key": gk, "kind": g["kind"], "name": g["name"], "uses": len(g["uses"]),
                    "passing_builds": passing_builds(g), "offered": g["offered"], "promoted": g["promoted"]})
    return out


def promote(kind: str, name: str, force: bool = False) -> dict:
    key = f"{kind}:{norm_name(name)}"
    with _locked():
        groups = _groups(read_events())
        g = groups.get(key) or next((v for v in groups.values() if key in v["keys"]), None)
        if g is None:
            return {"status": "REFUSED", "summary": f"no invention {key} in the ledger"}
        if not g["offered"] and not force:
            return {"status": "REFUSED", "summary": f"{key} has not passed in two builds yet (no offer recorded); "
                                                     f"pass --force to promote anyway"}
        uses = [u for u in g["uses"] if u.get("passed")] or g["uses"]
        latest = uses[-1]
        target = overlay_path(kind)
        lib = library_dir().resolve()
        if lib not in target.resolve().parents or PKG_ROOT.resolve() in target.resolve().parents:
            return {"status": "ERROR", "summary": f"refusing to write outside the user library: {target}"}
        entry_id = norm_name(g["name"])
        entry = {"id": entry_id, "name": g["name"], "kind": kind, "reason": latest.get("reason"),
                 "definition": latest.get("definition"), "promoted": _now(),
                 "builds": passing_builds(g), "source": "invention ledger (local, never shipped)"}
        backup = _merge_overlay(target, entry_id, entry)
        _append([{"event": "promoted", "ts": _now(), "kind": kind, "name": g["name"], "key": key,
                  "to": str(target)}])
    return {"status": "PASS", "summary": f"promoted {key} into {target}", "overlay": str(target),
            "backup": backup, "entry": entry_id}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 invention ledger (H5)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("build")
    r.add_argument("--build-id")
    r.add_argument("--gate-results", action="append", default=[])
    r.add_argument("--gate", action="append", default=[], help="NAME=STATUS")
    lp = sub.add_parser("list")
    lp.add_argument("--kind")
    sub.add_parser("offers")
    pp = sub.add_parser("promote")
    pp.add_argument("kind", choices=KINDS)
    pp.add_argument("name")
    pp.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "record":
            res = record(a.build, load_gate_results((), a.gate), a.build_id, a.gate_results)
            print(json.dumps(res, ensure_ascii=False))
            return {"PASS": 0, "SKIPPED": 2}.get(res["status"], 3)
        if a.cmd == "list":
            print(json.dumps({"inventions": listing(a.kind), "ledger": str(ledger_path())}, ensure_ascii=False))
            return 0
        if a.cmd == "offers":
            offs = [e for e in read_events() if e.get("event") == "offer"]
            print(json.dumps({"offers": offs}, ensure_ascii=False))
            return 0
        res = promote(a.kind, a.name, a.force)
        print(json.dumps(res, ensure_ascii=False))
        return {"PASS": 0, "REFUSED": 1}.get(res["status"], 3)
    except (OSError, ValueError) as e:
        print(json.dumps({"status": "ERROR", "summary": f"{type(e).__name__}: {e}"}))
        return 3


if __name__ == "__main__":
    sys.exit(main())
