#!/usr/bin/env python3
"""Coordn8 — look things up in the project index (K2): `coordn8 find <query>`.

    query.py <query>                      (repo = current directory)
    query.py <repo> <query> [--kind K] [--limit N] [--json] [--refresh] [--no-build]

Ranking, strongest first (lexical only — no embeddings, nothing leaves the machine):
    exact     the symbol name or qualified name equals the query (case-sensitive beats not)
    prefix    the name starts with the query
    token     every word of the query is a word of the name (camelCase / snake_case split)
    bm25      Okapi BM25 over doc chunks (heading words count double) and symbol names/docs

Output: one `path:line  kind  name — signature/heading` per hit. Each hit is checked against
the file on disk first (K3): a hit whose source changed since indexing is marked STALE.
`--refresh` re-indexes changed files before searching. When no index exists it is built
(unless --no-build).

--kind accepts any symbol kind (function, method, class, interface, type, enum, constant,
variable, heading, key, section, target, …) plus `doc` (chunks only), `entry` (entry
points only) and `code` (functions, methods, classes and TS declarations).

Exit codes: 0 hits · 1 no hits · 2 no index and --no-build · 3 error.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from index import read_json, read_jsonl  # noqa: E402
from index.freshness import flag_entries, hash_lookup  # noqa: E402
from lib.paths import project_research  # noqa: E402

TIER = {"exact": 4000, "exact-ci": 3500, "prefix": 2500, "token": 1500, "bm25": 0}
CODE_KINDS = {"function", "method", "class", "interface", "type", "enum", "namespace"}
KIND_BONUS = {"class": 40, "function": 35, "method": 30, "interface": 25, "type": 20, "enum": 20,
              "constant": 10, "variable": 5, "heading": 8, "target": 6, "key": 2, "section": 2}
STOP = set("a an the of to in on for and or is are be by with how what where why when which "
           "does do it this that from as at into".split())
K1, B = 1.5, 0.75


def words(s: str) -> List[str]:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s or "")
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w for w in re.split(r"[^A-Za-z0-9]+", s.lower()) if w]


def terms(s: str) -> List[str]:
    return [w for w in words(s) if w not in STOP]


class BM25:
    def __init__(self, docs: List[List[str]]):
        self.docs = docs
        self.n = len(docs)
        self.avg = (sum(len(d) for d in docs) / self.n) if self.n else 0.0
        self.df: Counter = Counter()
        for d in docs:
            self.df.update(set(d))
        self.tf = [Counter(d) for d in docs]

    def score(self, q: List[str], i: int) -> float:
        tf, dl, s = self.tf[i], len(self.docs[i]), 0.0
        for t in q:
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (self.n - self.df[t] + 0.5) / (self.df[t] + 0.5))
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / (self.avg or 1)))
        return s


def _symbol_tier(q: str, s: dict) -> Optional[str]:
    name, qual = s.get("name", ""), s.get("qualname", "")
    if q == name or q == qual:
        return "exact"
    ql = q.lower()
    if ql == name.lower() or ql == qual.lower():
        return "exact-ci"
    if name.lower().startswith(ql) or qual.lower().startswith(ql):
        return "prefix"
    qw = words(q)
    nw = set(words(qual)) | set(words(name))
    if qw and all(w in nw for w in qw):
        return "token"
    return None


def search(repo, query: str, kind: Optional[str] = None, limit: int = 10,
           refresh: bool = False, build_if_missing: bool = True) -> dict:
    repo = Path(repo).resolve()
    idx = project_research(repo) / "index"
    built = None
    if refresh or (build_if_missing and not (idx / "manifest.json").exists()):
        from index.build_index import build
        built = build(repo, incremental=True)
    if not (idx / "manifest.json").exists():
        return {"indexed": False, "hits": []}
    symbols = read_jsonl(idx / "symbols.jsonl")
    chunks = read_jsonl(idx / "chunks.jsonl")
    entries = (read_json(idx / "entrypoints.json", {}) or {}).get("entrypoints", [])
    q = query.strip()
    hits = []
    want_sym = kind not in ("doc", "entry")
    want_doc = kind in (None, "doc")
    want_ent = kind in (None, "entry")
    qt = terms(q) or words(q)
    if want_sym:
        pool = symbols
        if kind == "code":
            pool = [s for s in symbols if s["kind"] in CODE_KINDS]
        elif kind:
            pool = [s for s in symbols if s["kind"] == kind]
        sdocs = [terms(f"{s.get('qualname', '')} {s.get('doc', '')}") for s in pool]
        bm = BM25(sdocs) if pool else None
        for i, s in enumerate(pool):
            tier = _symbol_tier(q, s)
            if tier:
                score = TIER[tier] + KIND_BONUS.get(s["kind"], 0) - min(len(s["qualname"]), 60) * 0.5
            else:
                b = bm.score(qt, i) if bm else 0.0
                if b <= 0:
                    continue
                tier, score = "bm25", b * 10 + KIND_BONUS.get(s["kind"], 0) * 0.1
            hits.append({"type": "symbol", "tier": tier, "score": round(score, 3), "path": s["path"],
                         "line": s["line"], "kind": s["kind"], "name": s.get("qualname") or s["name"],
                         "signature": s.get("signature", ""), "sha": s.get("sha")})
    if want_doc and chunks:
        docs = []
        for c in chunks:
            head = " ".join(c.get("heading_path", []))
            docs.append(terms(head) * 2 + terms(c.get("text", "")) + terms(c["path"].rsplit("/", 1)[-1]))
        bm = BM25(docs)
        for i, c in enumerate(chunks):
            b = bm.score(qt, i)
            if b <= 0:
                continue
            hp = " › ".join(c.get("heading_path", [])) or c["path"]
            hits.append({"type": "chunk", "tier": "bm25", "score": round(b * 10, 3), "path": c["path"],
                         "line": c["line_start"], "line_end": c["line_end"], "kind": "doc",
                         "name": hp, "signature": re.sub(r"\s+", " ", c.get("text", ""))[:140],
                         "chunk_id": c["id"], "sha": c.get("sha")})
    if want_ent:
        ql = q.lower()
        for e in entries:
            hay = " ".join(str(e.get(k, "")) for k in ("kind", "name", "command", "path")).lower()
            if ql and (ql in hay or (qt and all(t in words(hay) for t in qt))):
                exact = ql in (str(e.get("name", "")).lower(), str(e.get("command", "")).lower())
                hits.append({"type": "entry", "tier": "exact" if exact else "token",
                             "score": (TIER["exact"] if exact else TIER["token"]) - 5, "path": e["path"],
                             "line": e["line"], "kind": "entry", "name": e.get("command") or e.get("name") or e["kind"],
                             "signature": e["kind"], "sha": e.get("sha")})
    hits.sort(key=lambda h: (-h["score"], h["path"], h["line"]))
    seen, uniq = set(), []
    for h in hits:
        key = (h["path"], h["line"], h["kind"], h["name"])
        if key not in seen:
            seen.add(key)
            uniq.append(h)
    top = flag_entries(repo, uniq[: max(1, limit)], hash_lookup(repo))
    return {"indexed": True, "query": q, "hits": top, "total": len(uniq),
            "stale": sum(1 for h in top if h.get("stale")), "built": bool(built)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Search the Coordn8 project index (K2).")
    ap.add_argument("args", nargs="+", help="[repo] query")
    ap.add_argument("--kind")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="re-index changed files first")
    ap.add_argument("--no-build", action="store_true", help="do not build a missing index")
    ap.add_argument("--repo", help="repository root (default: first arg if it is a directory, else .)")
    a = ap.parse_args(argv)
    args = list(a.args)
    repo = a.repo
    if repo is None:
        if len(args) >= 2 and Path(args[0]).is_dir():
            repo = args.pop(0)
        else:
            repo = "."
    query = " ".join(args)
    if not Path(repo).is_dir():
        print(f"[find] not a directory: {repo}", file=sys.stderr)
        return 3
    res = search(repo, query, kind=a.kind, limit=a.limit, refresh=a.refresh,
                 build_if_missing=not a.no_build)
    if not res["indexed"]:
        print("[find] no index — run: coordn8 index <repo>", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, indent=1, sort_keys=True))
    else:
        if not res["hits"]:
            print(f"[find] no hits for {query!r}")
        for h in res["hits"]:
            flag = "  STALE (source changed — re-index)" if h.get("stale") else ""
            print(f"{h['path']}:{h['line']}  {h['kind']}  {h['name']} — {h['signature']}{flag}")
    return 0 if res["hits"] else 1


if __name__ == "__main__":
    sys.exit(main())
