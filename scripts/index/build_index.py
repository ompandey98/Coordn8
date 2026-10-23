#!/usr/bin/env python3
"""Coordn8 — build the project retrieval index (K2), hash-stamped for freshness (K3),
and refresh the per-directory maps (K4).

    build_index.py <repo> [--incremental] [--no-ctags] [--no-maps] [--json]

Writes ONLY under <repo>/Coordn8/_research/index/ (see scripts/index/__init__.py for
the file list). Pure Python 3.9, zero third-party dependencies:

  * Python     — the standard `ast` module (functions, async functions, classes, methods,
                 nested functions, module constants; imports; calls; `__main__` guards).
  * JS / TS    — a small tokenizer blanks comments, strings, template text and regex
                 literals (newlines kept, so offsets and line numbers stay exact), then
                 patterns run over code only: function / class / method / arrow-function
                 consts / function expressions / TS interface, type, enum; ES imports,
                 re-exports, require(), dynamic import(); calls and `new X()`.
  * Docs       — Markdown / reStructuredText / text split by heading into chunks of at
                 most 1,500 characters, each with its heading path and line range.
  * Config     — top-level keys / sections of JSON, YAML, TOML, INI.
  * Other code — Universal Ctags when installed (shell functions use a built-in pattern).

Incremental (`--incremental`): every file is hashed (sha256); only files whose hash is new
are parsed again, everything else is reused from _parse_cache.jsonl. Removed files drop out
of every output. Cross-file resolution (which file an import or a call lands in) is cheap
and is recomputed from the per-file results on every run, so it is never stale.

Skipped and recorded in manifest.json["skipped"]: version-control dirs, node_modules,
virtual environments, caches, the repo's own Coordn8/ build folder, symlinks, binary
files and files over 1 MB.

Exit codes: 0 built · 3 error (bad repo path).
"""
from __future__ import annotations

import argparse
import ast
import bisect
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index import (INDEX_SCHEMA, PARSER_VERSION, atomic_write_text, read_json,  # noqa: E402
                   read_jsonl, sha256_bytes, write_jsonl)
from lib.paths import project_research  # noqa: E402

MAX_BYTES = 1_000_000
CHUNK_MAX = 1500

SKIP_DIR_NAMES = {
    ".git": "version control", ".hg": "version control", ".svn": "version control",
    "node_modules": "third-party packages", "bower_components": "third-party packages",
    "__pycache__": "bytecode cache", ".mypy_cache": "tool cache", ".pytest_cache": "tool cache",
    ".ruff_cache": "tool cache", ".tox": "virtual environment", ".nox": "virtual environment",
    ".venv": "virtual environment", "venv": "virtual environment",
    ".virtualenv": "virtual environment", "site-packages": "third-party packages",
    ".cache": "tool cache", ".next": "build output", ".nuxt": "build output",
}
BINARY_EXT = {
    "png", "jpg", "jpeg", "gif", "bmp", "ico", "icns", "webp", "tif", "tiff", "psd",
    "pdf", "zip", "gz", "tgz", "bz2", "xz", "7z", "rar", "tar", "jar", "war", "whl", "egg",
    "mp3", "mp4", "m4a", "mov", "avi", "mkv", "wav", "flac", "ogg", "webm",
    "woff", "woff2", "ttf", "otf", "eot", "so", "dylib", "dll", "exe", "bin", "o", "a",
    "class", "pyc", "pyo", "db", "sqlite", "sqlite3", "npy", "npz", "pkl", "onnx", "pt",
}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
              "Cargo.lock", "Gemfile.lock", "composer.lock", "uv.lock", "Pipfile.lock"}
LANG_BY_EXT = {
    "py": "python", "pyi": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "tsx": "typescript", "mts": "typescript", "cts": "typescript",
    "md": "markdown", "markdown": "markdown", "mdx": "markdown", "rst": "rst", "txt": "text",
    "json": "json", "yaml": "yaml", "yml": "yaml", "toml": "toml", "ini": "ini", "cfg": "ini",
    "sh": "shell", "bash": "shell", "zsh": "shell",
    "go": "go", "rs": "rust", "rb": "ruby", "java": "java", "kt": "kotlin", "c": "c",
    "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cs": "csharp", "php": "php",
    "swift": "swift", "scala": "scala", "lua": "lua", "pl": "perl", "r": "r",
    "html": "html", "htm": "html", "css": "css", "scss": "css", "sql": "sql",
}
CTAGS_LANGS = {"go", "rust", "ruby", "java", "kotlin", "c", "cpp", "csharp", "php",
               "swift", "scala", "lua", "perl", "r"}
JS_EXTS = (".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".d.ts")


# --------------------------------------------------------------------------- walking
def is_venv_dir(d: Path) -> bool:
    return (d / "pyvenv.cfg").is_file()


def walk_repo(repo: Path) -> Tuple[List[str], List[dict]]:
    """Return (indexable relative posix paths, skipped records). Deterministic order.
    The ONE walker — freshness.py uses it too, so both agree on what is indexed."""
    repo = Path(repo).resolve()
    files: List[str] = []
    skipped: List[dict] = []
    for dirpath, dirnames, filenames in os.walk(repo, followlinks=False):
        d = Path(dirpath)
        rel_dir = d.relative_to(repo).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = d / name
            reason = None
            if full.is_symlink():
                reason = "symlink (not followed)"
            elif name in SKIP_DIR_NAMES:
                reason = SKIP_DIR_NAMES[name]
            elif not rel_dir and name == "Coordn8":
                reason = "Coordn8 build folder (the index lives here)"
            elif is_venv_dir(full):
                reason = "virtual environment"
            if reason:
                skipped.append({"path": rel + "/", "reason": reason})
            else:
                keep.append(name)
        dirnames[:] = keep
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = d / name
            if full.is_symlink():
                skipped.append({"path": rel, "reason": "symlink (not followed)"})
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in BINARY_EXT:
                skipped.append({"path": rel, "reason": "binary"})
                continue
            try:
                size = full.stat().st_size
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if size > MAX_BYTES:
                skipped.append({"path": rel, "reason": f"over 1 MB ({size} bytes)"})
                continue
            try:
                with open(full, "rb") as fh:
                    head = fh.read(8192)
            except OSError:
                skipped.append({"path": rel, "reason": "unreadable"})
                continue
            if b"\x00" in head:
                skipped.append({"path": rel, "reason": "binary"})
                continue
            files.append(rel)
    return files, skipped


def lang_of(rel: str, head: bytes = b"") -> str:
    name = rel.rsplit("/", 1)[-1]
    if name in LOCK_NAMES:
        return "lockfile"
    if name == "Dockerfile" or name.startswith("Dockerfile.") or name.endswith(".dockerfile"):
        return "dockerfile"
    if name in ("Makefile", "makefile", "GNUmakefile") or name.endswith(".mk"):
        return "makefile"
    if name in ("Procfile",):
        return "procfile"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in LANG_BY_EXT:
        return LANG_BY_EXT[ext]
    if head.startswith(b"#!"):
        first = head.split(b"\n", 1)[0]
        if b"python" in first:
            return "python"
        if b"node" in first:
            return "javascript"
        if re.search(rb"\b(ba|z|k)?sh\b", first):
            return "shell"
    return "other"


def _first_line(text: Optional[str], limit: int = 140) -> str:
    if not text:
        return ""
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line[:limit]
    return ""


def _collapse(s: str, limit: int = 180) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


class Lines:
    """Offset -> 1-based line number."""

    def __init__(self, text: str):
        self.starts = [0] + [m.end() for m in re.finditer(r"\n", text)]

    def line(self, offset: int) -> int:
        return bisect.bisect_right(self.starts, offset)


# --------------------------------------------------------------------------- python
def _py_signature(node) -> str:
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    pre = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    sig = f"{pre} {node.name}({ast.unparse(node.args)})"
    if node.returns is not None:
        sig += f" -> {ast.unparse(node.returns)}"
    return _collapse(sig)


def _is_main_guard(node) -> bool:
    if not isinstance(node, ast.If):
        return False
    t = node.test
    if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], ast.Eq):
        sides = [t.left] + list(t.comparators)
        names = [s for s in sides if isinstance(s, ast.Name) and s.id == "__name__"]
        consts = [s for s in sides if isinstance(s, ast.Constant) and s.value == "__main__"]
        return bool(names and consts)
    return False


def _calls_in(nodes) -> List[dict]:
    """Calls in these statements, not descending into nested defs/classes."""
    out = []
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.append({"name": f.id, "base": None, "line": n.lineno})
            elif isinstance(f, ast.Attribute):
                base = None
                if isinstance(f.value, (ast.Name, ast.Attribute)):
                    try:
                        base = ast.unparse(f.value)[:80]
                    except Exception:  # noqa: BLE001
                        base = None
                out.append({"name": f.attr, "base": base, "line": n.lineno})
        stack.extend(ast.iter_child_nodes(n))
    return out


def parse_python(text: str) -> dict:
    res = {"symbols": [], "imports": [], "calls": [], "entries": [], "summary": "", "notes": []}
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as e:
        res["notes"].append(f"python syntax error, pattern fallback: {e.__class__.__name__}")
        for i, line in enumerate(text.splitlines(), 1):
            m = re.match(r"\s*(async\s+def|def|class)\s+([A-Za-z_]\w*)", line)
            if m:
                kind = "class" if m.group(1) == "class" else "function"
                res["symbols"].append({"name": m.group(2), "qualname": m.group(2), "kind": kind,
                                       "line": i, "signature": _collapse(line), "doc": ""})
        return res
    res["summary"] = _first_line(ast.get_docstring(tree))
    src_lines = text.splitlines()

    def visit(body, prefix: str, cls: Optional[str]):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{prefix}.{node.name}" if prefix else node.name
                kind = "method" if cls is not None and prefix == cls else "function"
                res["symbols"].append({"name": node.name, "qualname": qual, "kind": kind,
                                       "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
                                       "signature": _py_signature(node),
                                       "doc": _first_line(ast.get_docstring(node)),
                                       **({"async": True} if isinstance(node, ast.AsyncFunctionDef) else {})})
                for c in _calls_in(node.body):
                    c["from"] = qual
                    c["cls"] = cls if kind == "method" else None
                    res["calls"].append(c)
                visit(node.body, qual, None)
            elif isinstance(node, ast.ClassDef):
                qual = f"{prefix}.{node.name}" if prefix else node.name
                res["symbols"].append({"name": node.name, "qualname": qual, "kind": "class",
                                       "line": node.lineno, "end_line": getattr(node, "end_lineno", node.lineno),
                                       "signature": _py_signature(node),
                                       "doc": _first_line(ast.get_docstring(node))})
                visit(node.body, qual, qual)
            elif prefix == "" and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name) and not t.id.startswith("__"):
                        kind = "constant" if t.id.isupper() else "variable"
                        res["symbols"].append({"name": t.id, "qualname": t.id, "kind": kind,
                                               "line": node.lineno,
                                               "signature": _collapse(src_lines[node.lineno - 1] if node.lineno <= len(src_lines) else t.id, 120),
                                               "doc": ""})
            elif prefix == "" and _is_main_guard(node):
                res["entries"].append({"kind": "python __main__ guard", "line": node.lineno})
                for c in _calls_in(node.body):
                    c["from"] = "<module>"
                    c["cls"] = None
                    res["calls"].append(c)
                visit(node.body, prefix, cls)

    visit(tree.body, "", None)
    # module-level calls (outside defs)
    for c in _calls_in([n for n in tree.body if not _is_main_guard(n)]):
        c["from"] = "<module>"
        c["cls"] = None
        res["calls"].append(c)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                res["imports"].append({"module": a.name, "level": 0, "names": [],
                                       "alias": a.asname, "line": node.lineno})
        elif isinstance(node, ast.ImportFrom):
            res["imports"].append({"module": node.module or "", "level": node.level or 0,
                                   "names": [[a.name, a.asname] for a in node.names],
                                   "alias": None, "line": node.lineno})
    res["imports"].sort(key=lambda r: r["line"])
    return res


# --------------------------------------------------------------------------- javascript / typescript
JS_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "typeof", "instanceof",
    "new", "delete", "void", "throw", "yield", "await", "do", "else", "in", "of", "case",
    "super", "import", "export", "with", "constructor_", "class", "const", "let", "var",
    "try", "finally", "default", "extends", "implements", "interface", "type", "enum",
    "async", "static", "get", "set", "this", "require", "sizeof", "keyof",
}
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
_REGEX_WORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
                "void", "throw", "yield", "await", "instanceof"}


def js_mask(src: str) -> Tuple[str, Dict[int, str]]:
    """Blank comments, string contents, template text and regex literals to spaces.
    Newlines and every offset are preserved. Returns (masked, {quote_offset: literal})."""
    out = list(src)
    n = len(src)
    strings: Dict[int, str] = {}
    tpl: List[int] = []          # brace depth inside each open ${ ... }
    prev = ""                    # last significant code char
    word = ""                    # last identifier

    def blank(a: int, b: int):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    def scan_template(j: int) -> Tuple[int, bool]:
        """From inside a template at j: blank text; return (next index, opened_expr)."""
        start = j
        while j < n:
            ch = src[j]
            if ch == "\\":
                j += 2
                continue
            if ch == "`":
                blank(start, j)
                return j + 1, False
            if ch == "$" and j + 1 < n and src[j + 1] == "{":
                blank(start, j)
                return j + 2, True
            j += 1
        blank(start, n)
        return n, False

    i = 0
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j, n)
            strings[i] = src[i + 1:j]
            blank(i + 1, j)
            i = j + 1
            prev, word = '"', ""
            continue
        if c == "`":
            i, opened = scan_template(i + 1)
            if opened:
                tpl.append(0)
            prev, word = '"', ""
            continue
        if tpl and c == "{":
            tpl[-1] += 1
        elif tpl and c == "}":
            if tpl[-1] == 0:
                tpl.pop()
                i, opened = scan_template(i + 1)
                if opened:
                    tpl.append(0)
                prev, word = '"', ""
                continue
            tpl[-1] -= 1
        if c == "/" and (prev == "" or prev in _REGEX_PREV or word in _REGEX_WORDS):
            j = i + 1
            in_class = False
            ok = False
            while j < n and src[j] != "\n":
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    ok = True
                    break
                j += 1
            if ok and j > i + 1:
                blank(i + 1, j)
                i = j + 1
                while i < n and (src[i].isalpha()):
                    i += 1
                prev, word = '"', ""
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            word = src[i:j]
            prev = "a"
            i = j
            continue
        if not c.isspace():
            prev = c
            word = ""
        i += 1
    return "".join(out), strings


def _match_close(masked: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at i (or len-1)."""
    depth = 0
    for k in range(i, len(masked)):
        ch = masked[k]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return k
    return len(masked) - 1


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _skip_type(s: str, i: int, stops: str) -> int:
    """Skip a TS type annotation starting at i until a depth-0 char in `stops`
    (an `=>` inside the type is part of the type)."""
    depth = 0
    while i < len(s):
        ch = s[i]
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            if ch == ">" and i > 0 and s[i - 1] == "=":
                pass  # '=>' arrow inside a function type
            else:
                depth -= 1
                if depth < 0:
                    return i
        elif depth == 0 and ch in stops:
            if ch == "=" and i + 1 < len(s) and s[i + 1] in "=>":
                i += 2
                continue
            return i
        elif depth == 0 and ch == "\n" and ";" in stops:
            return i
        i += 1
    return i


def _initializer_kind(m: str, i: int) -> Tuple[str, int]:
    """At i (just after '='), classify the initializer: arrow / function / class / value.
    Returns (kind, offset of the body '{' or -1)."""
    i = _skip_ws(m, i)
    if m.startswith("async", i) and not (m[i + 5:i + 6].isalnum() or m[i + 5:i + 6] in "_$"):
        i = _skip_ws(m, i + 5)
    if m.startswith("function", i) and not (m[i + 8:i + 9].isalnum() or m[i + 8:i + 9] in "_$"):
        b = m.find("{", i)
        return "function", b
    if m.startswith("class", i) and not (m[i + 5:i + 6].isalnum() or m[i + 5:i + 6] in "_$"):
        return "class", m.find("{", i)
    j = i
    if j < len(m) and m[j] == "<":           # generic arrow  <T>(x: T) => ...
        j = _skip_ws(m, _match_close(m, j, "<", ">") + 1)
    if j < len(m) and m[j] == "(":
        j = _skip_ws(m, _match_close(m, j, "(", ")") + 1)
    elif j < len(m) and (m[j].isalpha() or m[j] in "_$"):
        while j < len(m) and (m[j].isalnum() or m[j] in "_$"):
            j += 1
        j = _skip_ws(m, j)
    else:
        return "value", -1
    if j < len(m) and m[j] == ":":           # return type runs to the depth-0 '=>'
        depth, j = 0, j + 1
        while j < len(m) and not (depth == 0 and m.startswith("=>", j)):
            if m[j] in "([{<":
                depth += 1
            elif m[j] in ")]}" or (m[j] == ">" and m[j - 1] != "="):
                depth -= 1
            elif m[j] in ";\n" and depth == 0:
                break
            j += 1
    if m.startswith("=>", j):
        k = _skip_ws(m, j + 2)
        return "arrow", (k if k < len(m) and m[k] == "{" else -1)
    return "value", -1


_ID = r"[A-Za-z_$][\w$]*"
RX_JS_FUNC = re.compile(rf"(?m)^[ \t]*(export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?(async[ \t]+)?function\b[ \t]*\*?[ \t]*({_ID})")
RX_JS_CLASS = re.compile(rf"(?m)^[ \t]*(export[ \t]+(?:default[ \t]+)?)?(?:declare[ \t]+)?(?:abstract[ \t]+)?class[ \t]+({_ID})")
RX_JS_VAR = re.compile(rf"(?m)^[ \t]*(export[ \t]+)?(?:declare[ \t]+)?(const|let|var)[ \t]+({_ID})[ \t]*")
RX_TS_DECL = re.compile(rf"(?m)^[ \t]*(export[ \t]+)?(?:declare[ \t]+)?(interface|type|enum|namespace)[ \t]+({_ID})")
RX_JS_METHOD = re.compile(
    rf"[ \t]*((?:(?:public|private|protected|static|readonly|async|override|abstract|declare|get|set)[ \t]+)*)"
    rf"(\*[ \t]*)?(#?{_ID})[ \t]*\??[ \t]*(<[^>(\n]*>)?[ \t]*([(=:])")
RX_JS_CALL = re.compile(rf"(?<![\w$.])(new[ \t]+)?({_ID})[ \t]*(?:<[^<>()\n]*>)?[ \t]*\(")
RX_JS_MCALL = re.compile(rf"(?<=[\w$\)\]])[ \t]*\??\.[ \t]*({_ID})[ \t]*\(")
RX_JS_IMPORT_FROM = re.compile(r"\b(import|export)((?:[ \t]+type)?[\s\w${},*]*?)\bfrom[ \t]*([\"'])")
RX_JS_IMPORT_BARE = re.compile(r"\bimport[ \t]*([\"'])")
RX_JS_IMPORT_DYN = re.compile(r"\bimport[ \t]*\([ \t]*([\"'`])")
RX_JS_REQUIRE = re.compile(rf"(?:\b(?:const|let|var)[ \t]+(\{{[^}}]*\}}|{_ID})[ \t]*=[ \t]*)?\brequire[ \t]*\([ \t]*([\"'])")
RX_JS_EXPORT_LIST = re.compile(r"\bexport[ \t]*\{([^}]*)\}")
RX_JS_EXPORT_DEFAULT = re.compile(rf"\bexport[ \t]+default[ \t]+({_ID})[ \t]*;?[ \t]*$", re.M)
RX_JS_MODULE_EXPORTS = re.compile(rf"\bmodule\.exports[ \t]*=[ \t]*(\{{[^}}]*\}}|{_ID})")
RX_JS_EXPORTS_PROP = re.compile(rf"(?m)^[ \t]*(?:module\.)?exports\.({_ID})[ \t]*=")


def _names_in_braces(s: str) -> List[Tuple[str, str]]:
    """'{ a, b as c, type d }' -> [(a,a),(b,c),(d,d)]  (original, local)."""
    out = []
    for part in s.strip().strip("{}").split(","):
        part = re.sub(r"^\s*type\s+", "", part).strip()
        if not part:
            continue
        m = re.match(rf"({_ID}|\*)(?:\s+as\s+({_ID}))?$", part)
        m2 = re.match(rf"({_ID})\s*:\s*({_ID})$", part)  # destructuring rename { a: b }
        if m:
            out.append((m.group(1), m.group(2) or m.group(1)))
        elif m2:
            out.append((m2.group(1), m2.group(2)))
    return out


def parse_js(text: str) -> dict:
    res = {"symbols": [], "imports": [], "calls": [], "entries": [], "summary": "", "notes": []}
    masked, strings = js_mask(text)
    L = Lines(text)
    # file summary: leading comment block
    m = re.match(r"\s*(?:#![^\n]*\n\s*)?(/\*\*?([\s\S]*?)\*/|((?:[ \t]*//[^\n]*\n)+))", text)
    if m:
        body = m.group(2) if m.group(2) is not None else re.sub(r"(?m)^[ \t]*//", "", m.group(3) or "")
        res["summary"] = _first_line(re.sub(r"(?m)^[ \t]*\*[ \t]?", "", body or ""))
    syms = []        # dicts with private keys _pos, _body
    def_pos = set()

    def sig_at(a: int, b: int) -> str:
        return _collapse(text[a:b])

    def add(name, kind, pos_name, start, sig_end, body_open, exported, extra=None):
        body_close = _match_close(masked, body_open, "{", "}") if body_open is not None and body_open >= 0 else -1
        s = {"name": name, "qualname": name, "kind": kind, "line": L.line(pos_name),
             "signature": sig_at(start, sig_end), "doc": "", "exported": bool(exported),
             "_pos": pos_name, "_body": (body_open, body_close) if body_close > 0 else None}
        if extra:
            s.update(extra)
        syms.append(s)
        def_pos.add(pos_name)
        return s

    for mm in RX_JS_FUNC.finditer(masked):
        brace = masked.find("{", mm.end())
        paren = masked.find("(", mm.end())
        if paren < 0:
            continue
        close = _match_close(masked, paren, "(", ")")
        nxt = _skip_ws(masked, close + 1)
        if nxt < len(masked) and masked[nxt] == ":":
            nxt = _skip_type(masked, nxt + 1, "{;")
        body = nxt if nxt < len(masked) and masked[nxt] == "{" else -1
        start = mm.start() + len(mm.group(0)) - len(mm.group(0).lstrip())
        add(mm.group(3), "function", mm.start(3), start, (body if body > 0 else close + 1),
            body, mm.group(1), {"async": True} if mm.group(2) else None)
        _ = brace
    for mm in RX_JS_CLASS.finditer(masked):
        brace = masked.find("{", mm.end())
        if brace < 0:
            continue
        start = mm.start() + len(mm.group(0)) - len(mm.group(0).lstrip())
        cls = add(mm.group(2), "class", mm.start(2), start, brace, brace, mm.group(1))
        if not cls["_body"]:
            continue
        b0, b1 = cls["_body"]
        # line starts at depth 1 inside the class body
        depth = 0
        k = b0
        line_starts = []
        while k <= b1:
            ch = masked[k]
            if ch == "{":
                depth += 1
                if depth == 1:
                    line_starts.append(k + 1)
            elif ch == "}":
                depth -= 1
            elif ch in "\n;" and depth == 1:
                line_starts.append(k + 1)
            k += 1
        for ls in line_starts:
            mt = RX_JS_METHOD.match(masked, ls)
            if not mt:
                continue
            name = mt.group(3)
            if name in JS_KEYWORDS and name not in ("get", "set", "static", "async"):
                continue
            if name == "constructor" or name not in JS_KEYWORDS:
                pass
            nstart = mt.start(3)
            opener = mt.group(5)
            mstart = mt.start() + len(mt.group(0)) - len(mt.group(0).lstrip())
            if opener == "(":
                close = _match_close(masked, mt.end() - 1, "(", ")")
                nxt = _skip_ws(masked, close + 1)
                if nxt < len(masked) and masked[nxt] == ":":
                    nxt = _skip_type(masked, nxt + 1, "{;")
                if nxt < len(masked) and masked[nxt] == "{":
                    add(name, "method", nstart, mstart, nxt, nxt, False,
                        {"qualname": f"{cls['name']}.{name}", "class": cls["name"],
                         **({"async": True} if "async" in (mt.group(1) or "") else {})})
            else:  # class field: name = ... / name: Type = ...
                eq = mt.end() - 1
                if opener == ":":
                    eq = _skip_type(masked, eq + 1, "=;")
                if eq < len(masked) and masked[eq] == "=":
                    kind, body = _initializer_kind(masked, eq + 1)
                    if kind in ("arrow", "function"):
                        end = body if body > 0 else masked.find("\n", eq)
                        add(name, "method", nstart, mstart, end, body, False,
                            {"qualname": f"{cls['name']}.{name}", "class": cls["name"], "arrow": kind == "arrow"})
    depth_pos, depth_val = 0, 0
    for mm in RX_JS_VAR.finditer(masked):
        seg = masked[depth_pos:mm.start()]
        depth_val += seg.count("{") - seg.count("}")
        depth_pos = mm.start()
        j = mm.end()
        if j < len(masked) and masked[j] == ":":
            j = _skip_type(masked, j + 1, "=;")
        if j >= len(masked) or masked[j] != "=" or masked[j + 1:j + 2] in ("=", ">"):
            continue
        kind, body = _initializer_kind(masked, j + 1)
        start = mm.start() + len(mm.group(0)) - len(mm.group(0).lstrip())
        if kind == "arrow" or kind == "function":
            end = body if body > 0 else (masked.find("\n", j) if masked.find("\n", j) > 0 else len(masked))
            add(mm.group(3), "function", mm.start(3), start, end, body, mm.group(1),
                {"arrow": kind == "arrow"})
        elif kind == "class":
            add(mm.group(3), "class", mm.start(3), start, body if body > 0 else j, body, mm.group(1))
        else:
            # only module-level (brace depth 0) plain values
            if depth_val == 0:
                end = masked.find("\n", j)
                add(mm.group(3), "constant" if mm.group(3).isupper() else "variable", mm.start(3),
                    start, end if end > 0 else len(masked), None, mm.group(1))
    for mm in RX_TS_DECL.finditer(masked):
        if mm.group(2) == "type":
            j = _skip_ws(masked, mm.end())
            if j < len(masked) and masked[j] == "<":
                j = _skip_ws(masked, _match_close(masked, j, "<", ">") + 1)
            if j >= len(masked) or masked[j] != "=":
                continue
        start = mm.start() + len(mm.group(0)) - len(mm.group(0).lstrip())
        end = masked.find("\n", mm.end())
        add(mm.group(3), mm.group(2), mm.start(3), start, end if end > 0 else len(masked), None, mm.group(1))
    for mm in RX_JS_EXPORTS_PROP.finditer(masked):
        j = masked.find("=", mm.end() - 1)
        kind, body = _initializer_kind(masked, j + 1)
        start = mm.start() + len(mm.group(0)) - len(mm.group(0).lstrip())
        end = body if body > 0 else masked.find("\n", j)
        add(mm.group(1), "function" if kind in ("arrow", "function") else "variable", mm.start(1),
            start, end if end > 0 else len(masked), body, True)

    # exports by list / default / module.exports
    exported = set()
    for mm in RX_JS_EXPORT_LIST.finditer(masked):
        if re.match(r"\s*from\b", masked[mm.end():mm.end() + 12]):
            continue
        exported.update(orig for orig, _ in _names_in_braces(mm.group(1)))
    for mm in RX_JS_EXPORT_DEFAULT.finditer(masked):
        exported.add(mm.group(1))
    for mm in RX_JS_MODULE_EXPORTS.finditer(masked):
        g = mm.group(1)
        exported.update(o for o, _ in _names_in_braces(g)) if g.startswith("{") else exported.add(g)
    for s in syms:
        if s["name"] in exported and s["kind"] != "method":
            s["exported"] = True

    # imports
    def lit(quote_pos: int) -> Optional[str]:
        return strings.get(quote_pos)

    for mm in RX_JS_IMPORT_FROM.finditer(masked):
        spec = lit(mm.end() - 1)
        if spec is None:
            continue
        clause = mm.group(2).strip()
        names = []
        if mm.group(1) == "import":
            ns = re.search(rf"\*\s*as\s+({_ID})", clause)
            if ns:
                names.append(["*", ns.group(1)])
            br = re.search(r"\{([^}]*)\}", clause)
            if br:
                names += [[o, loc] for o, loc in _names_in_braces(br.group(1))]
            dm = re.match(rf"(?:type\s+)?({_ID})\s*(?:,|$)", clause)
            if dm and dm.group(1) not in ("type",):
                names.append(["default", dm.group(1)])
        res["imports"].append({"module": spec, "names": names, "line": L.line(mm.start()),
                               "reexport": mm.group(1) == "export"})
    for rx in (RX_JS_IMPORT_BARE, RX_JS_IMPORT_DYN):
        for mm in rx.finditer(masked):
            spec = lit(mm.end() - 1)
            if spec is not None:
                res["imports"].append({"module": spec, "names": [], "line": L.line(mm.start())})
    for mm in RX_JS_REQUIRE.finditer(masked):
        spec = lit(mm.end() - 1)
        if spec is None:
            continue
        names = []
        b = mm.group(1)
        if b:
            names = ([[o, loc] for o, loc in _names_in_braces(b)] if b.startswith("{") else [["*", b]])
        res["imports"].append({"module": spec, "names": names, "line": L.line(mm.start())})
    res["imports"].sort(key=lambda r: r["line"])

    # calls, attributed to the innermost enclosing body (brace ranges nest, so one sweep
    # with a stack finds it in O(calls + bodies))
    bodies = sorted([(s["_body"][0], s["_body"][1], s["qualname"], s.get("class"))
                     for s in syms if s.get("_body")], key=lambda b: (b[0], -b[1]))
    raw = []
    for mm in RX_JS_CALL.finditer(masked):
        name = mm.group(2)
        if mm.start(2) in def_pos or (name in JS_KEYWORDS and not mm.group(1)):
            continue
        before = masked[max(0, mm.start() - 12):mm.start()]
        if re.search(r"\bfunction\s*\*?\s*$", before):
            continue
        raw.append((mm.start(), {"name": name, "base": None, "new": bool(mm.group(1)),
                                 "line": L.line(mm.start(2))}))
    for mm in RX_JS_MCALL.finditer(masked):
        name = mm.group(1)
        if name in JS_KEYWORDS:
            continue
        pre = masked[max(0, mm.start() - 40):mm.start() + 1]
        bm = re.search(rf"({_ID})\s*$", pre.rstrip(" \t"))
        raw.append((mm.start(), {"name": name, "base": bm.group(1) if bm else None,
                                 "line": L.line(mm.start(1))}))
    raw.sort(key=lambda r: r[0])
    stack: list = []
    bi = 0
    for pos, c in raw:
        while bi < len(bodies) and bodies[bi][0] <= pos:
            while stack and stack[-1][1] < bodies[bi][0]:
                stack.pop()
            stack.append(bodies[bi])
            bi += 1
        while stack and stack[-1][1] < pos:
            stack.pop()
        enc = stack[-1] if stack else None
        c["from"] = enc[2] if enc else "<module>"
        c["cls"] = enc[3] if enc else None
        res["calls"].append(c)
    for s in syms:
        s.pop("_pos", None)
        s.pop("_body", None)
    res["symbols"] = sorted(syms, key=lambda s: (s["line"], s["qualname"]))
    if text.startswith("#!"):
        res["entries"].append({"kind": "shebang executable", "line": 1,
                               "command": text.split("\n", 1)[0][2:].strip()})
    return res


# --------------------------------------------------------------------------- docs
RX_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
RX_FENCE = re.compile(r"^[ \t]{0,3}(```+|~~~+)")
RST_UNDER = re.compile(r"^([=\-~^\"'`#*+:.])\1{2,}\s*$")


def _split_long(lines: List[str], first_line: int, limit: int) -> List[Tuple[int, int, str]]:
    """Split a section into pieces of at most `limit` chars, preferring blank-line
    boundaries, then line boundaries, then a hard cut. Returns (start, end, text)."""
    pieces = []
    cur: List[str] = []
    cur_start = first_line
    size = 0

    def flush(end_line):
        nonlocal cur, size, cur_start
        if cur:
            pieces.append((cur_start, end_line, "\n".join(cur)))
        cur, size = [], 0

    # group lines into paragraphs
    paras: List[Tuple[int, List[str]]] = []
    buf: List[str] = []
    buf_start = first_line
    for k, line in enumerate(lines):
        ln = first_line + k
        if not buf:
            buf_start = ln
        buf.append(line)
        if not line.strip():
            paras.append((buf_start, buf))
            buf = []
    if buf:
        paras.append((buf_start, buf))
    for p_start, p_lines in paras:
        p_text_len = sum(len(x) + 1 for x in p_lines)
        if size + p_text_len > limit and cur:
            flush(cur_start + len(cur) - 1)
        if p_text_len > limit:
            for k, line in enumerate(p_lines):
                ln = p_start + k
                while len(line) > limit:
                    if cur:
                        flush(cur_start + len(cur) - 1)
                    pieces.append((ln, ln, line[:limit]))
                    line = line[limit:]
                if size + len(line) + 1 > limit and cur:
                    flush(cur_start + len(cur) - 1)
                if not cur:
                    cur_start = ln
                cur.append(line)
                size += len(line) + 1
            continue
        if not cur:
            cur_start = p_start
        cur.extend(p_lines)
        size += p_text_len
    flush(cur_start + len(cur) - 1)
    return pieces


def parse_doc(text: str, lang: str) -> dict:
    res = {"symbols": [], "imports": [], "calls": [], "entries": [], "summary": "", "chunks": [], "notes": []}
    lines = text.splitlines()
    heads: List[Tuple[int, int, str]] = []    # (line_idx, level, title)
    in_fence = None
    rst_levels: List[str] = []
    for k, line in enumerate(lines):
        fm = RX_FENCE.match(line)
        if fm:
            mark = fm.group(1)[0]
            if in_fence is None:
                in_fence = mark
            elif in_fence == mark:
                in_fence = None
            continue
        if in_fence:
            continue
        if lang == "markdown":
            m = RX_ATX.match(line)
            if m:
                heads.append((k, len(m.group(1)), m.group(2).strip()))
            elif k > 0 and lines[k - 1].strip() and re.match(r"^(=+|-+)\s*$", line) and not RX_ATX.match(lines[k - 1]) \
                    and (k - 1, ) not in [(h[0],) for h in heads[-1:]] and len(line.strip()) >= 3:
                if not re.match(r"^[-*+]\s", lines[k - 1]) and not lines[k - 1].startswith("|"):
                    heads.append((k - 1, 1 if line.strip()[0] == "=" else 2, lines[k - 1].strip()))
        elif lang == "rst":
            if k > 0 and lines[k - 1].strip() and RST_UNDER.match(line) and len(line.strip()) >= len(lines[k - 1].strip()):
                ch = line.strip()[0]
                if ch not in rst_levels:
                    rst_levels.append(ch)
                heads.append((k - 1, rst_levels.index(ch) + 1, lines[k - 1].strip()))
    heads.sort()
    # section boundaries: content of a heading runs to the next heading
    sections = []
    title_skip = {}
    if not heads or heads[0][0] > 0:
        sections.append((0, (heads[0][0] if heads else len(lines)), None))
    for idx, (k, lvl, title) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        sections.append((k, end, (lvl, title)))
        if lang == "rst":
            title_skip[k] = 2
    path_stack: List[Tuple[int, str]] = []
    for (a, b, head) in sections:
        if head is not None:
            lvl, title = head
            while path_stack and path_stack[-1][0] >= lvl:
                path_stack.pop()
            path_stack.append((lvl, title))
            res["symbols"].append({"name": title, "qualname": " > ".join(t for _, t in path_stack),
                                   "kind": "heading", "line": a + 1, "signature": "#" * lvl + " " + title,
                                   "doc": "", "level": lvl})
        hp = [t for _, t in path_stack]
        body = lines[a:b]
        if not "".join(body).strip():
            continue
        for (s, e, chunk) in _split_long(body, a + 1, CHUNK_MAX):
            if not chunk.strip():
                continue
            res["chunks"].append({"heading_path": hp, "line_start": s, "line_end": e,
                                  "text": chunk})
    if heads:
        res["summary"] = heads[0][2][:140]
        # first paragraph after the first heading gives a better purpose line
        k0 = heads[0][0] + (2 if lang == "rst" else 1)
        para = []
        for line in lines[k0:k0 + 30]:
            if RX_ATX.match(line) or RX_FENCE.match(line):
                if para:
                    break
                continue
            if not line.strip():
                if para:
                    break
                continue
            if line.lstrip().startswith(("|", "<", "![", "[!")):
                continue
            para.append(line.strip())
        if para:
            res["lead"] = _collapse(" ".join(para), 220)
    else:
        res["summary"] = _first_line(text)
    return res


# --------------------------------------------------------------------------- config / build files
def parse_config(text: str, lang: str, rel: str) -> dict:
    res = {"symbols": [], "imports": [], "calls": [], "entries": [], "summary": "", "notes": []}
    lines = text.splitlines()
    if lang == "json":
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            for key in data:
                ln = 1
                for k, line in enumerate(lines, 1):
                    if re.match(rf'^\s{{0,4}}"{re.escape(str(key))}"\s*:', line):
                        ln = k
                        break
                res["symbols"].append({"name": str(key), "qualname": str(key), "kind": "key", "line": ln,
                                       "signature": _collapse(f'"{key}": {json.dumps(data[key])}', 120), "doc": ""})
            if rel.rsplit("/", 1)[-1] == "package.json":
                res["summary"] = str(data.get("description") or "")[:140]
                res["package_json"] = {k: data.get(k) for k in ("name", "main", "module", "bin", "scripts")
                                       if data.get(k) is not None}
                res["package_json"]["_lines"] = {}
                for k in ("main", "module", "bin", "scripts"):
                    for i, line in enumerate(lines, 1):
                        if re.match(rf'^\s*"{k}"\s*:', line):
                            res["package_json"]["_lines"][k] = i
                            break
    elif lang == "yaml":
        for k, line in enumerate(lines, 1):
            m = re.match(r"^([A-Za-z_][\w.\-]*)\s*:", line)
            if m:
                res["symbols"].append({"name": m.group(1), "qualname": m.group(1), "kind": "key", "line": k,
                                       "signature": _collapse(line, 120), "doc": ""})
    elif lang in ("toml", "ini"):
        section = ""
        for k, line in enumerate(lines, 1):
            m = re.match(r"^\s*\[+\s*([^\]]+?)\s*\]+\s*$", line)
            if m:
                section = m.group(1)
                res["symbols"].append({"name": section, "qualname": section, "kind": "section", "line": k,
                                       "signature": line.strip(), "doc": ""})
                continue
            km = re.match(r"^\s*([A-Za-z0-9_.\-\"']+)\s*[=:]\s*(.*)$", line)
            if km and not section:
                key = km.group(1).strip("\"'")
                res["symbols"].append({"name": key, "qualname": key, "kind": "key", "line": k,
                                       "signature": _collapse(line, 120), "doc": ""})
            if km and section in ("project.scripts", "tool.poetry.scripts", "project.gui-scripts",
                                  "console_scripts", "options.entry_points"):
                val = km.group(2).strip().strip("\"'")
                if ":" in val:
                    res["entries"].append({"kind": "console script", "line": k,
                                           "name": km.group(1).strip("\"'"), "target": val})
        # setup.cfg  [options.entry_points] console_scripts =\n    name = mod:func
        if rel.rsplit("/", 1)[-1] == "setup.cfg":
            block = re.search(r"(?ms)^console_scripts\s*=\s*\n((?:[ \t]+.+\n?)+)", text)
            if block:
                base = text[:block.start(1)].count("\n") + 1
                for off, line in enumerate(block.group(1).splitlines()):
                    mm = re.match(r"\s*([\w.\-]+)\s*=\s*([\w.]+:[\w.]+)", line)
                    if mm:
                        res["entries"].append({"kind": "console script", "line": base + off,
                                               "name": mm.group(1), "target": mm.group(2)})
    elif lang == "dockerfile":
        for k, line in enumerate(lines, 1):
            m = re.match(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", line, re.I)
            if m:
                res["entries"].append({"kind": f"Dockerfile {m.group(1).upper()}", "line": k,
                                       "command": _collapse(m.group(2), 160)})
    elif lang == "makefile":
        for k, line in enumerate(lines, 1):
            m = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_.\-/]*)\s*:(?!=)", line)
            if m and not line.startswith("\t"):
                res["symbols"].append({"name": m.group(1), "qualname": m.group(1), "kind": "target", "line": k,
                                       "signature": _collapse(line, 120), "doc": ""})
                res["entries"].append({"kind": "make target", "line": k, "name": m.group(1),
                                       "command": f"make {m.group(1)}"})
    elif lang == "procfile":
        for k, line in enumerate(lines, 1):
            m = re.match(r"^([\w\-]+):\s*(.+)$", line)
            if m:
                res["entries"].append({"kind": "Procfile process", "line": k, "name": m.group(1),
                                       "command": _collapse(m.group(2), 160)})
    elif lang == "shell":
        for k, line in enumerate(lines, 1):
            m = re.match(r"^\s*(?:function\s+)?([A-Za-z_][\w\-]*)\s*\(\s*\)\s*\{?", line) or \
                re.match(r"^\s*function\s+([A-Za-z_][\w\-]*)\s*\{", line)
            if m:
                res["symbols"].append({"name": m.group(1), "qualname": m.group(1), "kind": "function", "line": k,
                                       "signature": _collapse(line, 120), "doc": ""})
        m = re.match(r"#![^\n]*\n((?:#[^\n]*\n)+)", text)
        if m:
            res["summary"] = _first_line(re.sub(r"(?m)^#\s?", "", m.group(1)))
    if rel.rsplit("/", 1)[-1] == "setup.py":
        pass
    if text.startswith("#!") and lang != "python":
        res["entries"].append({"kind": "shebang executable", "line": 1,
                               "command": text.split("\n", 1)[0][2:].strip()})
    return res


def parse_setup_py_entries(text: str) -> List[dict]:
    out = []
    for m in re.finditer(r"[\"']([\w.\-]+)\s*=\s*([\w.]+:[\w.]+)[\"']", text):
        out.append({"kind": "console script", "line": text.count("\n", 0, m.start()) + 1,
                    "name": m.group(1), "target": m.group(2)})
    return out


# --------------------------------------------------------------------------- ctags
def ctags_bin() -> Optional[str]:
    if os.environ.get("COORDN8_INDEX_NO_CTAGS"):
        return None
    exe = shutil.which("ctags")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return exe if "Universal Ctags" in (r.stdout or "") else None


def run_ctags(exe: str, repo: Path, rels: List[str]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {r: [] for r in rels}
    if not rels:
        return out
    try:
        r = subprocess.run([exe, "--output-format=json", "--fields=+nKS", "-f", "-", "-L", "-"],
                           input="\n".join(rels) + "\n", capture_output=True, text=True,
                           cwd=str(repo), timeout=120)
    except (OSError, subprocess.SubprocessError):
        return out
    if r.returncode != 0:
        return out
    for line in r.stdout.splitlines():
        try:
            t = json.loads(line)
        except ValueError:
            continue
        if t.get("_type") != "tag" or t.get("path") not in out:
            continue
        scope = t.get("scope")
        name = t.get("name", "")
        out[t["path"]].append({"name": name, "qualname": f"{scope}.{name}" if scope else name,
                               "kind": str(t.get("kind", "symbol")), "line": int(t.get("line") or 1),
                               "signature": _collapse(name + (t.get("signature") or ""), 160), "doc": "",
                               "via": "ctags"})
    return out


# --------------------------------------------------------------------------- per-file parse
def header_comment(text: str) -> str:
    """First line of a leading `#` / `//` / `--` / `/* */` comment block (after a shebang)."""
    t = text
    if t.startswith("#!"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
    t = t.lstrip("\ufeff")
    m = re.match(r"\s*/\*\*?([\s\S]*?)\*/", t)
    if m:
        return _first_line(re.sub(r"(?m)^[ \t]*\*[ \t]?", "", m.group(1)))
    m = re.match(r"((?:[ \t]*(?://|#|--|;)[^\n]*\n?)+)", t)
    if m:
        body = re.sub(r"(?m)^[ \t]*(?://+|#+|--|;+)[ \t]?", "", m.group(1))
        return _first_line(body)
    return ""


def parse_file(text: str, lang: str, rel: str) -> dict:
    r = _parse_file(text, lang, rel)
    if not r.get("summary") and lang not in ("markdown", "rst", "text", "json", "lockfile"):
        r["summary"] = header_comment(text)
    return r


def _parse_file(text: str, lang: str, rel: str) -> dict:
    if lang == "python":
        r = parse_python(text)
        if rel.rsplit("/", 1)[-1] == "setup.py":
            r["entries"] += parse_setup_py_entries(text)
        if text.startswith("#!"):
            r["entries"].append({"kind": "shebang executable", "line": 1,
                                 "command": text.split("\n", 1)[0][2:].strip()})
        return r
    if lang in ("javascript", "typescript"):
        return parse_js(text)
    if lang in ("markdown", "rst", "text"):
        return parse_doc(text, lang)
    if lang in ("json", "yaml", "toml", "ini", "dockerfile", "makefile", "procfile", "shell"):
        return parse_config(text, lang, rel)
    r = {"symbols": [], "imports": [], "calls": [], "entries": [], "summary": "", "notes": []}
    if text.startswith("#!"):
        r["entries"].append({"kind": "shebang executable", "line": 1,
                             "command": text.split("\n", 1)[0][2:].strip()})
    return r


# --------------------------------------------------------------------------- resolution
def py_module_name(rel: str, all_files: set) -> Optional[Tuple[str, bool]]:
    """'shop/cart.py' -> ('shop.cart', False); 'shop/__init__.py' -> ('shop', True).
    The import root is the nearest ancestor directory that has no __init__.py."""
    if not rel.endswith((".py", ".pyi")):
        return None
    parts = rel[: rel.rfind(".")].split("/")
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    if not parts:
        return None
    dir_parts = parts if is_pkg else parts[:-1]
    i = len(dir_parts)
    while i > 0 and "/".join(dir_parts[:i]) + "/__init__.py" in all_files:
        i -= 1
    name = ".".join(parts[i:])
    return (name, is_pkg) if name else None


def resolve_js_spec(spec: str, from_rel: str, all_files: set) -> Optional[str]:
    if not spec.startswith("."):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), spec))
    cands = [base]
    stem = base
    for ext in (".js", ".jsx", ".mjs", ".cjs"):
        if base.endswith(ext):
            stem = base[: -len(ext)]
            cands += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
    cands += [base + e for e in JS_EXTS]
    cands += [f"{base}/index{e}" for e in JS_EXTS]
    for c in cands:
        if c in all_files:
            return c
    return None


def assemble(repo: Path, files: Dict[str, dict], parsed: Dict[str, dict]) -> dict:
    """Build symbols / chunks / xref / entrypoints from per-file parse results."""
    all_files = set(files)
    modules: Dict[str, str] = {}
    mod_of: Dict[str, Tuple[str, bool]] = {}
    for rel in sorted(all_files):
        if files[rel]["lang"] == "python":
            mn = py_module_name(rel, all_files)
            if mn:
                mod_of[rel] = mn
                modules.setdefault(mn[0], rel)
    symbols, chunks, xref, entries = [], [], [], []
    by_file_qual: Dict[str, Dict[str, dict]] = {}
    by_name: Dict[str, List[Tuple[str, dict]]] = {}
    for rel in sorted(parsed):
        sha = files[rel]["sha"]
        lang = files[rel]["lang"]
        r = parsed[rel]
        qmap = {}
        for s in r.get("symbols", []):
            row = dict(s)
            row.update({"path": rel, "lang": lang, "sha": sha})
            row.setdefault("qualname", row["name"])
            symbols.append(row)
            qmap.setdefault(row["qualname"], row)
            if row["kind"] not in ("heading", "key", "section", "target"):
                by_name.setdefault(row["name"], []).append((rel, row))
        by_file_qual[rel] = qmap
        for n, c in enumerate(r.get("chunks", [])):
            chunks.append({"id": f"{rel}#{n + 1}", "path": rel, "heading_path": c["heading_path"],
                           "line_start": c["line_start"], "line_end": c["line_end"],
                           "text": c["text"], "sha": sha})

    def py_abs_module(rel: str, imp: dict) -> str:
        if not imp.get("level"):
            return imp["module"]
        mn = mod_of.get(rel)
        if not mn:
            return imp["module"]
        name, is_pkg = mn
        parts = name.split(".")
        pkg = parts if is_pkg else parts[:-1]
        up = imp["level"] - 1
        pkg = pkg[: len(pkg) - up] if up else pkg
        return ".".join(pkg + ([imp["module"]] if imp["module"] else []))

    def mod_path(mod: str) -> Optional[str]:
        return modules.get(mod)

    for rel in sorted(parsed):
        sha = files[rel]["sha"]
        lang = files[rel]["lang"]
        r = parsed[rel]
        local: Dict[str, Tuple[Optional[str], str]] = {}   # local name -> (target path, original)
        local_mod: Dict[str, Optional[str]] = {}           # local name -> module file path
        seen_imp = set()
        for imp in r.get("imports", []):
            if lang == "python":
                absmod = py_abs_module(rel, imp)
                targets = []
                if imp.get("names"):
                    for orig, alias in imp["names"]:
                        sub = f"{absmod}.{orig}" if absmod else orig
                        if mod_path(sub):
                            targets.append((sub, mod_path(sub)))
                            local_mod[alias or orig] = mod_path(sub)
                        else:
                            local[alias or orig] = (mod_path(absmod), orig)
                    if not targets:
                        targets.append((absmod, mod_path(absmod)))
                else:
                    targets.append((absmod, mod_path(absmod)))
                    if imp.get("alias"):
                        local_mod[imp["alias"]] = mod_path(absmod)
                    else:
                        local_mod[absmod.split(".")[0]] = mod_path(absmod.split(".")[0])
                        local_mod[absmod] = mod_path(absmod)
                for to, to_path in targets:
                    key = (to, imp["line"])
                    if key in seen_imp:
                        continue
                    seen_imp.add(key)
                    xref.append({"from": rel, "to": to, "kind": "import", "path": rel, "line": imp["line"],
                                 "to_path": to_path, "external": to_path is None, "sha": sha})
            elif lang in ("javascript", "typescript"):
                to_path = resolve_js_spec(imp["module"], rel, all_files)
                for orig, loc in imp.get("names", []):
                    if orig == "*":
                        local_mod[loc] = to_path
                    else:
                        local[loc] = (to_path, orig)
                key = (imp["module"], imp["line"])
                if key in seen_imp:
                    continue
                seen_imp.add(key)
                xref.append({"from": rel, "to": imp["module"], "kind": "import", "path": rel,
                             "line": imp["line"], "to_path": to_path, "external": to_path is None,
                             "sha": sha, **({"reexport": True} if imp.get("reexport") else {})})
        # calls
        own = by_file_qual.get(rel, {})
        seen_call = set()
        for c in r.get("calls", []):
            name, base = c["name"], c.get("base")
            target = None
            how = None
            if base in ("self", "cls", "this") and c.get("cls"):
                q = f"{c['cls']}.{name}"
                if q in own:
                    target, how = (rel, own[q]), "self"
            elif base is None:
                if name in own and own[name]["kind"] not in ("variable", "constant", "heading", "key"):
                    target, how = (rel, own[name]), "same-file"
                elif name in local:
                    tp, orig = local[name]
                    if orig == "default" and tp:
                        cand = [s for s in by_file_qual.get(tp, {}).values()
                                if s.get("exported") and s["kind"] in ("class", "function")]
                        if len(cand) == 1:
                            target, how = (tp, cand[0]), "import"
                    elif tp and orig in by_file_qual.get(tp, {}):
                        target, how = (tp, by_file_qual[tp][orig]), "import"
            elif base is not None and base in local_mod and local_mod[base]:
                tp = local_mod[base]
                if name in by_file_qual.get(tp, {}):
                    target, how = (tp, by_file_qual[tp][name]), "module-attr"
            if target is None and base is None:
                cands = [x for x in by_name.get(name, []) if x[1]["kind"] in ("function", "class")]
                if len(cands) == 1:
                    target, how = cands[0], "unique-name"
            if target is None:
                continue
            tp, ts = target
            if tp == rel and ts["qualname"] == c["from"]:
                how = how  # recursion is still an edge
            frm = f"{rel}::{c['from']}"
            to = f"{tp}::{ts['qualname']}"
            if (frm, to) in seen_call:
                continue
            seen_call.add((frm, to))
            xref.append({"from": frm, "to": to, "kind": "call", "path": rel, "line": c["line"],
                         "to_path": tp, "to_line": ts["line"], "how": how, "sha": sha})
        # entry points
        for e in r.get("entries", []):
            row = {"kind": e["kind"], "path": rel, "line": e["line"], "sha": sha}
            for k in ("name", "command", "target"):
                if e.get(k):
                    row[k] = e[k]
            if e["kind"] == "python __main__ guard":
                mn = mod_of.get(rel)
                in_pkg = mn and "." in mn[0]
                row["command"] = f"python -m {mn[0]}" if in_pkg else f"python {rel}"
            if e["kind"] == "console script" and e.get("target"):
                mod, _, func = e["target"].partition(":")
                tp = mod_path(mod)
                if tp:
                    row["target_path"] = tp
                    fs = by_file_qual.get(tp, {}).get(func.split(".")[0])
                    if fs:
                        row["target_line"] = fs["line"]
                row["command"] = e["name"]
            entries.append(row)
        if rel.endswith("/__main__.py") or rel == "__main__.py":
            mn = mod_of.get(rel)
            pkg = mn[0].rsplit(".", 1)[0] if mn and "." in mn[0] else posixpath.dirname(rel)
            entries.append({"kind": "python package __main__", "path": rel, "line": 1, "sha": sha,
                            "command": f"python -m {pkg}"})
        pj = r.get("package_json")
        if pj:
            pdir = posixpath.dirname(rel)
            lines = pj.get("_lines", {})

            def target(p):
                t = posixpath.normpath(posixpath.join(pdir, p)) if isinstance(p, str) else None
                return t if t in all_files else None

            for k in ("main", "module"):
                if isinstance(pj.get(k), str):
                    entries.append({"kind": f"package.json {k}", "path": rel, "line": lines.get(k, 1),
                                    "name": pj[k], "target_path": target(pj[k]), "sha": sha})
            b = pj.get("bin")
            if isinstance(b, str):
                b = {str(pj.get("name") or posixpath.basename(pdir) or "bin"): b}
            if isinstance(b, dict):
                for name, p in b.items():
                    entries.append({"kind": "package.json bin", "path": rel, "line": lines.get("bin", 1),
                                    "name": name, "command": name, "target_path": target(p), "sha": sha})
            if isinstance(pj.get("scripts"), dict):
                for name, cmd in pj["scripts"].items():
                    entries.append({"kind": "npm script", "path": rel, "line": lines.get("scripts", 1),
                                    "name": name, "command": f"npm run {name}", "script": str(cmd)[:160],
                                    "sha": sha})
    symbols.sort(key=lambda s: (s["path"], s["line"], s["qualname"]))
    xref.sort(key=lambda x: (x["path"], x["line"], x["kind"], x["to"]))
    entries.sort(key=lambda e: (e["path"], e["line"], e["kind"]))
    return {"symbols": symbols, "chunks": chunks, "xref": xref, "entrypoints": entries,
            "modules": modules}


# --------------------------------------------------------------------------- build
def index_dir(repo: Path) -> Path:
    return project_research(repo) / "index"


def build(repo, incremental: bool = False, use_ctags: bool = True, maps: bool = True) -> dict:
    t0 = time.time()
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise NotADirectoryError(str(repo))
    out_dir = index_dir(repo)
    prev_manifest = read_json(out_dir / "manifest.json", {}) if incremental else {}
    prev_files = (prev_manifest or {}).get("files", {})
    cache_rows = read_jsonl(out_dir / "_parse_cache.jsonl") if incremental else []
    cache = {r["path"]: r for r in cache_rows
             if r.get("parser_version") == PARSER_VERSION and isinstance(r.get("result"), dict)}
    cache_by_sha = {}
    for r in cache.values():
        cache_by_sha.setdefault(r["sha"], r)
    exe = ctags_bin() if use_ctags else None

    rels, skipped = walk_repo(repo)
    files: Dict[str, dict] = {}
    parsed: Dict[str, dict] = {}
    reparsed, reused, added, changed = [], 0, [], []
    need_ctags: List[str] = []
    new_cache = []
    for rel in rels:
        full = repo / rel
        try:
            data = full.read_bytes()
            st = full.stat()
        except OSError:
            skipped.append({"path": rel, "reason": "unreadable"})
            continue
        if b"\x00" in data[:8192]:
            skipped.append({"path": rel, "reason": "binary"})
            continue
        sha = sha256_bytes(data)
        lang = lang_of(rel, data[:200])
        files[rel] = {"sha": sha, "size": len(data), "lang": lang, "mtime": round(st.st_mtime, 3)}
        if rel not in prev_files:
            added.append(rel)
        elif prev_files[rel].get("sha") != sha:
            changed.append(rel)
        wants_ctags = lang in CTAGS_LANGS and exe is not None
        hit = cache.get(rel)
        if not (hit and hit["sha"] == sha):
            hit = cache_by_sha.get(sha) if (rel not in prev_files) else None
        if hit and hit["sha"] == sha and bool(hit.get("ctags")) == wants_ctags and hit.get("lang") == lang:
            parsed[rel] = hit["result"]
            reused += 1
        else:
            text = data.decode("utf-8", errors="replace")
            try:
                parsed[rel] = parse_file(text, lang, rel)
            except RecursionError:
                parsed[rel] = {"symbols": [], "imports": [], "calls": [], "entries": [],
                               "summary": "", "notes": ["parser recursion limit"]}
            reparsed.append(rel)
            if wants_ctags:
                need_ctags.append(rel)
        new_cache.append({"path": rel, "sha": sha, "lang": lang, "parser_version": PARSER_VERSION,
                          "ctags": wants_ctags, "result": parsed[rel]})
    if need_ctags and exe:
        tags = run_ctags(exe, repo, need_ctags)
        for rel, syms in tags.items():
            parsed[rel]["symbols"] = parsed[rel].get("symbols", []) + syms
    removed = sorted(p for p in prev_files if p not in files)
    for rel in files:
        r = parsed[rel]
        if r.get("summary"):
            files[rel]["summary"] = r["summary"]
        if r.get("lead"):
            files[rel]["lead"] = r["lead"]
    asm = assemble(repo, files, parsed)
    out_dir.mkdir(parents=True, exist_ok=True)
    elapsed = round(time.time() - t0, 3)
    run = {"mode": "incremental" if (incremental and prev_files) else "full",
           "files": len(files), "reparsed": reparsed, "reused": reused,
           "added": added if prev_files else [], "changed": changed, "removed": removed,
           "ctags": bool(exe), "seconds": elapsed}
    manifest = {"schema": INDEX_SCHEMA, "parser_version": PARSER_VERSION,
                "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "root": ".",
                "files": files, "skipped": sorted(skipped, key=lambda s: s["path"]),
                "counts": {"files": len(files), "symbols": len(asm["symbols"]),
                           "chunks": len(asm["chunks"]), "xref": len(asm["xref"]),
                           "entrypoints": len(asm["entrypoints"]), "skipped": len(skipped)},
                "run": run}
    write_jsonl(out_dir / "symbols.jsonl", asm["symbols"])
    write_jsonl(out_dir / "chunks.jsonl", asm["chunks"])
    write_jsonl(out_dir / "xref.jsonl", asm["xref"])
    atomic_write_text(out_dir / "entrypoints.json",
                      json.dumps({"schema": INDEX_SCHEMA, "entrypoints": asm["entrypoints"]},
                                 indent=1, sort_keys=True) + "\n")
    write_jsonl(out_dir / "_parse_cache.jsonl", new_cache)
    if maps:
        from index import maps as maps_mod
        mres = maps_mod.build_maps(repo, manifest=manifest, symbols=asm["symbols"],
                                   xref=asm["xref"], entrypoints=asm["entrypoints"])
        run["maps"] = {"rebuilt": mres["rebuilt"], "kept": mres["kept"], "removed": mres["removed"]}
    run["seconds"] = round(time.time() - t0, 3)
    manifest["run"] = run
    atomic_write_text(out_dir / "manifest.json", json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return {"index_dir": str(out_dir), "counts": manifest["counts"], "run": run}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the Coordn8 project index (K2/K3/K4).")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--incremental", action="store_true",
                    help="re-parse only files whose sha256 changed (falls back to full if no index)")
    ap.add_argument("--no-ctags", action="store_true", help="never call Universal Ctags")
    ap.add_argument("--no-maps", action="store_true", help="skip directory maps")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        res = build(a.repo, incremental=a.incremental, use_ctags=not a.no_ctags, maps=not a.no_maps)
    except (NotADirectoryError, FileNotFoundError) as e:
        print(f"[index] not a directory: {e}", file=sys.stderr)
        return 3
    if a.json:
        print(json.dumps(res, indent=1, sort_keys=True))
    else:
        r = res["run"]
        c = res["counts"]
        print(f"[index] {r['mode']}: {c['files']} files, {c['symbols']} symbols, {c['chunks']} doc chunks, "
              f"{c['xref']} links, {c['entrypoints']} entry points; re-parsed {len(r['reparsed'])}, "
              f"reused {r['reused']}, removed {len(r['removed'])}, skipped {c['skipped']} "
              f"in {r['seconds']}s -> {res['index_dir']}")
        if "maps" in r:
            print(f"[index] maps: rebuilt {len(r['maps']['rebuilt'])}, kept {r['maps']['kept']}, "
                  f"removed {len(r['maps']['removed'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
