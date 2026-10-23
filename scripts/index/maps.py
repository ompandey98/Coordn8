#!/usr/bin/env python3
"""Coordn8 — one short map per directory (K4): purpose, key files, how it connects.

    maps.py <repo>                 rebuild maps whose inputs changed (uses the built index)
    maps.py <repo> --show <dir>    print one map ('.' for the repo root)
    maps.py <repo> --json          machine-readable result

Written to <repo>/Coordn8/_research/index/maps/:
    <dir>.md       one per directory ('_root.md' for the top, 'a/b' -> 'a__b.md')
    INDEX.md       every directory with its one-line purpose — read this first
    _state.json    dir -> {file, signature, files: {name: sha}}

A map is rebuilt ONLY when its inputs changed: the hashes of the files directly in that
directory, its sub-folder names, its entry points and its cross-directory links. Editing a
file therefore rebuilds that file's directory, plus any directory whose links to it changed —
nothing else. Every map ends with the signature and file count it was built from, so a map is
as checkable for freshness as any other index entry (K3).

Purpose lines are derived mechanically, never by a model: the folder's README lead paragraph,
else its package docstring, else the first module docstring / header comment in the folder,
else a factual one-liner (what languages, what it defines).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index import PARSER_VERSION, atomic_write_text, read_json, read_jsonl  # noqa: E402
from lib.paths import project_research  # noqa: E402

MAX_KEY_FILES = 8
MAX_DEFS_PER_FILE = 5
MAX_LINKS = 6
README_NAMES = ("README.md", "readme.md", "README.rst", "README.txt", "README", "index.md")
CODE_LANGS = {"python", "javascript", "typescript", "shell", "go", "rust", "ruby", "java",
              "kotlin", "c", "cpp", "csharp", "php", "swift", "scala", "lua", "perl", "r"}


def maps_dir(repo) -> Path:
    return project_research(repo) / "index" / "maps"


def map_filename(d: str, taken: Dict[str, str]) -> str:
    base = "_root" if d in ("", ".") else d.replace("/", "__")
    name = base + ".md"
    if name in taken and taken[name] != d:
        name = f"{base}-{hashlib.sha256(d.encode()).hexdigest()[:6]}.md"
    taken[name] = d
    return name


def _dir(p: str) -> str:
    return posixpath.dirname(p) or "."


def _label(d: str) -> str:
    return "(repo root)" if d == "." else f"{d}/"


def _purpose(d: str, here: List[str], files: dict, symbols_by_path: dict) -> tuple:
    """(purpose sentence, where it came from)."""
    by_name = {posixpath.basename(p): p for p in here}
    for rn in README_NAMES:
        p = by_name.get(rn)
        if p:
            f = files[p]
            lead = f.get("lead") or f.get("summary")
            if lead:
                return lead, posixpath.basename(p)
    for meta in ("package.json", "pyproject.toml", "Cargo.toml"):
        p = by_name.get(meta)
        if p and files[p].get("summary"):
            return files[p]["summary"], meta
    for init in ("__init__.py", "index.ts", "index.js", "mod.rs", "doc.go"):
        p = by_name.get(init)
        if p and files[p].get("summary"):
            return files[p]["summary"], init
    code = [p for p in here if files[p]["lang"] in CODE_LANGS | {"yaml", "toml", "ini"} and files[p].get("summary")]
    code.sort(key=lambda p: (-len(symbols_by_path.get(p, [])), p))
    if code:
        return files[code[0]]["summary"], posixpath.basename(code[0])
    docs = [p for p in here if files[p]["lang"] in ("markdown", "rst") and files[p].get("summary")]
    if docs:
        p = sorted(docs)[0]
        return files[p].get("lead") or files[p]["summary"], posixpath.basename(p)
    langs = Counter(files[p]["lang"] for p in here)
    if not here:
        return "Groups sub-folders only; no files of its own.", "derived"
    top = ", ".join(f"{n} {lang}" for lang, n in langs.most_common(3))
    defs = [s["name"] for p in here for s in symbols_by_path.get(p, [])
            if s["kind"] in ("class", "function")][:4]
    tail = f"; defines {', '.join(defs)}" if defs else ""
    return f"Holds {len(here)} file{'s' if len(here) != 1 else ''} ({top}){tail}.", "derived"


def compute_inputs(manifest: dict, symbols: list, xref: list, entrypoints: list) -> dict:
    files = manifest.get("files", {})
    dirs: Dict[str, List[str]] = defaultdict(list)
    subdirs: Dict[str, set] = defaultdict(set)
    for p in files:
        d = _dir(p)
        dirs[d].append(p)
        # register every ancestor
        while d != ".":
            parent = _dir(d)
            subdirs[parent].add(posixpath.basename(d))
            dirs.setdefault(parent, [])
            d = parent
    dirs.setdefault(".", [])
    sym_by_path: Dict[str, list] = defaultdict(list)
    for s in symbols:
        sym_by_path[s["path"]].append(s)
    uses: Dict[str, Counter] = defaultdict(Counter)
    used_by: Dict[str, Counter] = defaultdict(Counter)
    inner: Dict[str, Counter] = defaultdict(Counter)
    external: Dict[str, Counter] = defaultdict(Counter)
    inbound_file: Counter = Counter()
    for x in xref:
        src = _dir(x["path"])
        tp = x.get("to_path")
        if tp:
            dst = _dir(tp)
            if tp != x["path"]:
                inbound_file[tp] += 1
            if dst == src:
                if tp != x["path"]:
                    inner[src][(posixpath.basename(x["path"]), posixpath.basename(tp))] += 1
            else:
                uses[src][dst] += 1
                used_by[dst][src] += 1
        elif x["kind"] == "import" and not x["to"].startswith(".") and x["to"]:
            t = x["to"]
            if t.startswith("@"):
                top = "/".join(t.split("/")[:2])
            else:
                top = t.split("/")[0]
                if x["path"].endswith((".py", ".pyi")):
                    top = top.split(".")[0]
            external[src][top] += 1
    ent_by_dir: Dict[str, list] = defaultdict(list)
    for e in entrypoints:
        ent_by_dir[_dir(e["path"])].append(e)
    return {"files": files, "dirs": dirs, "subdirs": subdirs, "sym_by_path": sym_by_path,
            "uses": uses, "used_by": used_by, "inner": inner, "external": external,
            "inbound_file": inbound_file, "ent_by_dir": ent_by_dir}


def signature(d: str, I: dict) -> str:
    here = sorted(I["dirs"].get(d, []))
    payload = {
        "parser": PARSER_VERSION,
        "files": [[posixpath.basename(p), I["files"][p]["sha"], I["files"][p].get("summary", ""),
                   I["files"][p].get("lead", ""),
                   sorted([s["kind"], s["name"]] for s in I["sym_by_path"].get(p, []))] for p in here],
        "subdirs": sorted(I["subdirs"].get(d, [])),
        "uses": sorted(I["uses"].get(d, {}).items()),
        "used_by": sorted(I["used_by"].get(d, {}).items()),
        "inner": sorted([list(k), v] for k, v in I["inner"].get(d, {}).items()),
        "external": sorted(I["external"].get(d, {}).items()),
        "entries": sorted([e["kind"], posixpath.basename(e["path"]), e["line"], e.get("name") or e.get("command") or ""]
                          for e in I["ent_by_dir"].get(d, [])),
        "inbound": sorted([posixpath.basename(p), I["inbound_file"][p]] for p in here if I["inbound_file"].get(p)),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def render(d: str, I: dict, sig: str) -> str:
    files = I["files"]
    here = sorted(I["dirs"].get(d, []))
    purpose, src = _purpose(d, here, files, I["sym_by_path"])
    ent_paths = {e["path"] for e in I["ent_by_dir"].get(d, [])}

    def rank(p):
        n_defs = sum(1 for s in I["sym_by_path"].get(p, []) if s["kind"] not in ("heading", "key"))
        return (0 if p in ent_paths else 1, 0 if posixpath.basename(p) in README_NAMES else 1,
                -(I["inbound_file"].get(p, 0) * 3 + n_defs), p)

    ranked = sorted(here, key=rank)
    out = [f"# {_label(d)}", "", f"**Purpose.** {purpose}" + ("" if src == "derived" else f" _(from {src})_"), ""]
    if ranked:
        out.append(f"**Key files** ({len(here)} here)")
        for p in ranked[:MAX_KEY_FILES]:
            f = files[p]
            defs = [s for s in I["sym_by_path"].get(p, []) if s["kind"] in
                    ("class", "function", "interface", "type", "enum", "target")]
            note = f.get("summary") or ""
            bits = [f"- `{posixpath.basename(p)}` ({f['lang']})"]
            if note:
                bits.append(f" — {note[:110].rstrip('.')}")
            if defs:
                names = ", ".join(s["name"] for s in defs[:MAX_DEFS_PER_FILE])
                more = f" +{len(defs) - MAX_DEFS_PER_FILE}" if len(defs) > MAX_DEFS_PER_FILE else ""
                bits.append(f". Defines {names}{more}")
            if I["inbound_file"].get(p):
                bits.append(f". Linked from {I['inbound_file'][p]} place(s)")
            out.append("".join(bits))
        rest = ranked[MAX_KEY_FILES:]
        if rest:
            lc = Counter(files[p]["lang"] for p in rest)
            out.append(f"- …and {len(rest)} more ({', '.join(f'{n} {k}' for k, n in lc.most_common(4))})")
        out.append("")
    subs = sorted(I["subdirs"].get(d, []))
    if subs:
        out.append("**Sub-folders:** " + ", ".join(f"`{s}/`" for s in subs[:20])
                   + (f" +{len(subs) - 20}" if len(subs) > 20 else ""))
        out.append("")
    conn = []
    u = I["uses"].get(d)
    if u:
        conn.append("- uses: " + ", ".join(f"`{_label(k)}` ({v})" for k, v in u.most_common(MAX_LINKS)))
    ub = I["used_by"].get(d)
    if ub:
        conn.append("- used by: " + ", ".join(f"`{_label(k)}` ({v})" for k, v in ub.most_common(MAX_LINKS)))
    inner = I["inner"].get(d)
    if inner:
        conn.append("- inside: " + ", ".join(f"{a} → {b}" for (a, b), _ in inner.most_common(MAX_LINKS)))
    ext = I["external"].get(d)
    if ext:
        conn.append("- outside packages: " + ", ".join(k for k, _ in ext.most_common(MAX_LINKS)))
    ents = I["ent_by_dir"].get(d, [])
    if ents:
        shown = []
        for e in sorted(ents, key=lambda e: (e["path"], e["line"]))[:MAX_LINKS]:
            what = e.get("command") or e.get("name") or e["kind"]
            shown.append(f"`{what}` ({e['kind']}, {posixpath.basename(e['path'])}:{e['line']})")
        conn.append("- entry points: " + "; ".join(shown))
    if conn:
        out.append("**Connections**")
        out.extend(conn)
        out.append("")
    out.append(f"_Built from {len(here)} file(s); signature {sig[:12]}. Regenerated when a file here, "
               f"a sub-folder name or a link changes._")
    return "\n".join(out) + "\n"


def build_maps(repo, manifest: Optional[dict] = None, symbols: Optional[list] = None,
               xref: Optional[list] = None, entrypoints: Optional[list] = None,
               force: bool = False) -> dict:
    repo = Path(repo).resolve()
    idx = project_research(repo) / "index"
    if manifest is None:
        manifest = read_json(idx / "manifest.json", {}) or {}
    if symbols is None:
        symbols = read_jsonl(idx / "symbols.jsonl")
    if xref is None:
        xref = read_jsonl(idx / "xref.jsonl")
    if entrypoints is None:
        entrypoints = (read_json(idx / "entrypoints.json", {}) or {}).get("entrypoints", [])
    I = compute_inputs(manifest, symbols, xref, entrypoints)
    md = maps_dir(repo)
    state = read_json(md / "_state.json", {}) or {}
    old = state.get("dirs", {})
    taken: Dict[str, str] = {}
    for d in sorted(old):
        taken.setdefault(old[d]["file"], d)
    rebuilt, kept = [], 0
    new_state: Dict[str, dict] = {}
    purposes = {}
    for d in sorted(I["dirs"]):
        fname = old[d]["file"] if d in old else map_filename(d, taken)
        sig = signature(d, I)
        here = sorted(I["dirs"][d])
        purposes[d] = _purpose(d, here, I["files"], I["sym_by_path"])[0]
        entry = {"file": fname, "signature": sig,
                 "files": {posixpath.basename(p): I["files"][p]["sha"] for p in here}}
        if not force and d in old and old[d].get("signature") == sig and (md / fname).exists():
            kept += 1
        else:
            atomic_write_text(md / fname, render(d, I, sig))
            rebuilt.append(d)
        new_state[d] = entry
    removed = []
    for d, e in old.items():
        if d not in new_state:
            removed.append(d)
            try:
                (md / e["file"]).unlink()
            except OSError:
                pass
    lines = ["# Directory maps", "",
             "Read the map of a folder before opening its files. One line per folder:", ""]
    for d in sorted(new_state, key=lambda x: (x != ".", x)):
        lines.append(f"- [{_label(d)}]({new_state[d]['file']}) — {purposes[d][:140]}")
    index_text = "\n".join(lines) + "\n"
    ip = md / "INDEX.md"
    try:
        same = ip.read_text(encoding="utf-8") == index_text
    except OSError:
        same = False
    if not same:
        atomic_write_text(ip, index_text)
    atomic_write_text(md / "_state.json", json.dumps({"schema": 1, "dirs": new_state}, indent=1,
                                                     sort_keys=True) + "\n")
    return {"maps_dir": str(md), "rebuilt": rebuilt, "kept": kept, "removed": sorted(removed),
            "dirs": len(new_state)}


def show(repo, d: str) -> Optional[str]:
    md = maps_dir(repo)
    state = (read_json(md / "_state.json", {}) or {}).get("dirs", {})
    d = "." if d in ("", ".", "./") else d.strip("/")
    e = state.get(d)
    if not e:
        return None
    try:
        return (md / e["file"]).read_text(encoding="utf-8")
    except OSError:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-directory maps for the Coordn8 index (K4).")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--show", metavar="DIR")
    ap.add_argument("--force", action="store_true", help="rewrite every map")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    if not (project_research(repo) / "index" / "manifest.json").exists():
        print("[maps] no index yet — run: coordn8 index <repo>", file=sys.stderr)
        return 2
    if a.show is not None:
        text = show(repo, a.show)
        if text is None:
            print(f"[maps] no map for {a.show!r}", file=sys.stderr)
            return 1
        print(text, end="")
        return 0
    res = build_maps(repo, force=a.force)
    if a.json:
        print(json.dumps(res, indent=1, sort_keys=True))
    else:
        print(f"[maps] {res['dirs']} folders: rebuilt {len(res['rebuilt'])}, kept {res['kept']}, "
              f"removed {len(res['removed'])} -> {res['maps_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
