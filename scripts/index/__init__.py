"""Coordn8 project retrieval index (K2 index, K3 freshness, K4 directory maps).

Everything is written under <repo>/Coordn8/_research/index/ — the user-owned project
folder — and nowhere else in the repo:

    manifest.json      path -> {sha, size, lang, mtime} + skipped files and run counts
    symbols.jsonl      {name, qualname, kind, path, line, signature, lang, sha}
    chunks.jsonl       doc sections {id, path, heading_path, line_start, line_end, text, sha}
    xref.jsonl         {from, to, kind: import|call, path, line, to_path, sha}
    entrypoints.json   __main__ blocks, package.json bin/main/scripts, console scripts,
                       Dockerfile CMD/ENTRYPOINT, Makefile targets, shebang executables
    maps/<dir>.md      one short map per directory (+ maps/INDEX.md, maps/_state.json)
    _parse_cache.jsonl per-file parse results, so an incremental run re-parses only
                       files whose sha256 changed

Pure Python 3.9, zero third-party dependencies. Universal Ctags is used only when it is
installed (for languages other than Python / JavaScript / TypeScript / docs). Nothing is
ever sent to a hosted service: there are no embeddings, ranking is lexical (exact name >
prefix > token > BM25 over doc chunks).

This module holds the small helpers shared by build_index / query / maps / freshness.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, Optional, Union

INDEX_SCHEMA = 1
PARSER_VERSION = 3          # bump to force a full re-parse of cached files

INDEX_FILES = ("manifest.json", "symbols.jsonl", "chunks.jsonl", "xref.jsonl",
               "entrypoints.json")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Union[str, Path]) -> Optional[str]:
    """sha256 hex of a file's bytes, or None if it cannot be read.
    Byte-for-byte the same digest as scripts/memory/store.py file_hash()."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    lines = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
    atomic_write_text(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def read_jsonl(path: Path) -> list:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
