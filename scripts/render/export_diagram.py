#!/usr/bin/env python3
"""Coordn8 — export one diagram as SVG + PNG + a registry entry (B7).

    python3 scripts/render/export_diagram.py <graph.json | layout.json> --build DIR
            [--kit KIT.json | design_system.json | runtime.json] [--dagre PATH] [--id ID]
            [--type NAME] [--plan PATH] [--rankdir TB|BT|LR|RL] [--scale 2] [--no-png]

1. lays the graph out with scripts/render/layout.js (via node) — or takes an existing layout JSON
2. renders the SVG in the active kit (kit_export runtime; a kit or design_system is converted)
3. runs the connector gate (scripts/gates/check_connectors.py) on the layout
4. rasterises the SVG to PNG with a headless browser (network blocked); skipped honestly — with
   the reason recorded — when no browser is found
5. writes/replaces {id, type, nodes, edges, svg_sha256, png, source_plan, ...} in
   <build>/diagrams/registry.json (no timestamps: the same input gives the same registry)

The vendored layout library is checked against its pinned sha256 (scripts/render/fetch_visual_libs.py)
before it runs. Exit: 0 exported, 2 SKIPPED (no node / no layout library), 3 ERROR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_SKIPPED, emit, find_chrome, run  # noqa: E402

CLI = HERE / "layout_cli.js"
GATE = SCRIPTS / "gates" / "check_connectors.py"


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def pinned_dagre_sha() -> str | None:
    try:
        sys.path.insert(0, str(HERE))
        import fetch_visual_libs  # type: ignore
        return fetch_visual_libs.LIBS["dagre"]["sha256"]
    except Exception:  # noqa: BLE001
        return None


def find_dagre(arg: str | None, build: Path) -> Path | None:
    cands = [arg, os.environ.get("COORDN8_DAGRE"), build / "vendor" / "dagre.min.js",
             Path(__file__).resolve().parent / "vendor" / "dagre.min.js",
             build / "_build" / "vendor" / "dagre.min.js", build.parent / "_build" / "vendor" / "dagre.min.js"]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    return None


def runtime_kit(path: str | None, tmp: Path) -> Path | None:
    """A kit_export runtime JSON on disk (converted from a kit / design_system when needed)."""
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data.get("colors"), dict) and "props" not in data:
        return Path(path)
    sys.path.insert(0, str(SCRIPTS / "design"))
    from design.kit_export import load_active, to_runtime  # type: ignore  # noqa: E402
    kit, skin = load_active(path)
    out = tmp / "kit.runtime.json"
    out.write_text(json.dumps(to_runtime(kit, skin), indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def png_size(p: Path):
    b = Path(p).read_bytes()[:32]
    if len(b) < 24 or b[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", b[16:24])


def rasterise(svg: Path, png: Path, w: int, h: int, scale: float, tmp: Path):
    """(ok, reason). Headless browser, network cut at the resolver, throwaway profile."""
    chrome = find_chrome()
    if not chrome:
        return False, ("no headless browser found (install one sudo-free: `pip install playwright && "
                       "python3 -m playwright install chromium`)")
    prof = tmp / "profile"
    prof.mkdir(exist_ok=True)
    cmd = [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
           "--no-default-browser-check", f"--user-data-dir={prof}", "--host-resolver-rules=MAP * ~NOTFOUND",
           f"--force-device-scale-factor={scale:g}", f"--window-size={w},{h}",
           f"--screenshot={png}", svg.resolve().as_uri()]
    cp = run(cmd, timeout=90)
    size = png_size(png) if png.exists() else None
    if not size:
        return False, f"browser ran (exit {cp.returncode}) but wrote no PNG: {(cp.stderr or '')[-200:]}"
    if size != (int(round(w * scale)), int(round(h * scale))):
        return True, f"PNG is {size[0]}x{size[1]}, expected {int(w * scale)}x{int(h * scale)}"
    return True, None


def plan_entry(plan: Path | None, fig_id: str):
    if not plan or not plan.is_file():
        return None
    try:
        doc = json.loads(plan.read_text(encoding="utf-8"))
    except ValueError:
        return None
    for f in doc.get("figures") or []:
        if isinstance(f, dict) and f.get("id") == fig_id:
            return f
    return None


def write_registry(reg: Path, entry: dict) -> None:
    doc = {"schema": 1, "diagrams": []}
    if reg.exists():
        try:
            old = json.loads(reg.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("diagrams"), list):
                doc = old
        except ValueError:
            pass
    items = [d for d in doc["diagrams"] if isinstance(d, dict)]
    for i, d in enumerate(items):
        if d.get("id") == entry["id"]:
            items[i] = entry
            break
    else:
        items.append(entry)
    doc["diagrams"] = items
    tmp = reg.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(reg))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export a diagram: SVG, PNG and a registry entry.")
    ap.add_argument("source", help="graph JSON or a layout JSON from layout_cli.js")
    ap.add_argument("--build", required=True, help="build dir; writes <build>/diagrams/")
    ap.add_argument("--kit", help="style kit, design_system.json or kit_export runtime JSON")
    ap.add_argument("--dagre", help="vendored dagre.min.js (default: $COORDN8_DAGRE or <build>/vendor/)")
    ap.add_argument("--allow-unpinned", action="store_true", help="run a layout library whose sha256 is not the pinned one")
    ap.add_argument("--id")
    ap.add_argument("--type", help="diagram type (catalogued or invented); default from the plan or the graph")
    ap.add_argument("--plan", help="diagram_plan.json (default <build>/diagram_plan.json)")
    ap.add_argument("--rankdir")
    ap.add_argument("--scale", type=float, default=2.0)
    ap.add_argument("--no-png", action="store_true")
    a = ap.parse_args(argv)

    node = shutil.which("node")
    if not node:
        emit({"gate": "export_diagram", "status": "SKIPPED", "summary": "node is not installed; the layout engine needs Node 17+"})
        return EXIT_SKIPPED
    build = Path(a.build)
    out_dir = build / "diagrams"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        src = json.loads(Path(a.source).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        emit({"gate": "export_diagram", "status": "ERROR", "summary": f"cannot read {a.source}: {ex}"})
        return EXIT_ERROR
    is_layout = isinstance(src, dict) and isinstance(src.get("paint_order"), list) and isinstance(src.get("bbox"), dict)
    fig_id = str(a.id or src.get("id") or Path(a.source).stem)
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in fig_id) or "diagram"

    with tempfile.TemporaryDirectory(prefix="c8-export-") as td:
        tmp = Path(td)
        try:
            kit_rt = runtime_kit(a.kit, tmp)
        except Exception as ex:  # noqa: BLE001
            emit({"gate": "export_diagram", "status": "ERROR", "summary": f"kit could not be exported: {ex}"})
            return EXIT_ERROR
        layout_json = out_dir / f"{slug}.layout.json"
        svg = out_dir / f"{slug}.svg"
        cmd = [node, str(CLI), str(Path(a.source).resolve()), "--svg", str(svg), "--json", str(layout_json)]
        dagre_info = None
        if not is_layout:
            dagre = find_dagre(a.dagre, build)
            if not dagre:
                emit({"gate": "export_diagram", "status": "SKIPPED",
                      "summary": "the vendored layout library is missing — `python3 scripts/render/fetch_visual_libs.py --only dagre`"})
                return EXIT_SKIPPED
            got, pin = sha256_file(dagre), pinned_dagre_sha()
            if pin and got != pin and not a.allow_unpinned:
                emit({"gate": "export_diagram", "status": "ERROR",
                      "summary": f"{dagre} does not match the pinned sha256 ({got[:12]}… != {pin[:12]}…); refusing to run it"})
                return EXIT_ERROR
            dagre_info = {"sha256": got, "pinned": bool(pin and got == pin)}
            cmd += ["--dagre", str(dagre), "--id", fig_id]
            if a.rankdir:
                cmd += ["--rankdir", a.rankdir]
        if kit_rt:
            cmd += ["--kit", str(kit_rt)]
        cp = run(cmd, timeout=120)
        if cp.returncode != 0 or not svg.exists():
            emit({"gate": "export_diagram", "status": "ERROR", "summary": f"layout failed: {(cp.stderr or '').strip()[-300:]}"})
            return EXIT_ERROR
        lay = json.loads(layout_json.read_text(encoding="utf-8"))
        gcp = run([sys.executable, str(GATE), str(layout_json)] + (["--kit", str(kit_rt)] if kit_rt else []), timeout=120)
        try:
            gate = json.loads((gcp.stdout or "").strip().splitlines()[0])
        except (ValueError, IndexError):
            gate = {"status": "ERROR", "summary": (gcp.stderr or "")[-200:]}

        png = out_dir / f"{slug}.png"
        png_rel, png_note = None, None
        if a.no_png:
            png_note = "skipped: --no-png"
        else:
            w, h = int(lay["bbox"]["w"]), int(lay["bbox"]["h"])
            ok, reason = rasterise(svg, png, w, h, a.scale, tmp)
            if ok:
                png_rel = png.name
                png_note = reason
            else:
                png_note = f"skipped: {reason}"
                if png.exists():
                    png.unlink()

    plan_path = Path(a.plan) if a.plan else build / "diagram_plan.json"
    pe = plan_entry(plan_path, fig_id)
    dtype = a.type or (pe or {}).get("type") or lay.get("type")
    entry = {
        "id": fig_id,
        "type": dtype,
        "invented": (pe or {}).get("invented"),
        "nodes": [n["id"] for n in lay["nodes"]],
        "edges": [{"from": e["from"], "to": e["to"], "label": (e.get("label") or {}).get("text")} for e in lay["edges"]],
        "svg": svg.name,
        "svg_sha256": sha256_file(svg),
        "png": png_rel,
        "png_note": png_note,
        "layout": layout_json.name,
        "source_plan": f"{plan_path.name}#{fig_id}" if pe else None,
        "kit": None,
        "connector_gate": {"status": gate.get("status"), "counts": gate.get("counts")},
        "engine": lay.get("engine"),
        "layout_library": dagre_info,
    }
    if a.kit:
        try:
            k = json.loads(Path(a.kit).read_text(encoding="utf-8"))
            entry["kit"] = k.get("kit") or k.get("id") or (k.get("kit_spec") or {}).get("id")
        except (OSError, ValueError):
            entry["kit"] = None
    write_registry(out_dir / "registry.json", entry)
    emit({"gate": "export_diagram", "status": "PASS", "id": fig_id, "svg": str(svg), "png": str(png) if png_rel else None,
          "png_note": png_note, "connector_gate": gate.get("status"), "registry": str(out_dir / "registry.json"),
          "summary": f"exported {fig_id}: svg{' + png' if png_rel else ''} + registry entry (connector gate {gate.get('status')})"})
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
