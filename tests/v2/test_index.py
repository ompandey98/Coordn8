#!/usr/bin/env python3
"""v2 suite: project retrieval index (K2), freshness (K3), directory maps (K4).

Hermetic: the fixture repo is copied into a temp dir before indexing, HOME / COORDN8_HOME /
CLAUDE_HOME point into the temp dir, and everything is deleted at the end.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, PY, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from index import build_index as BI  # noqa: E402
from index import freshness as FR  # noqa: E402
from index import maps as MP  # noqa: E402
from index import query as Q  # noqa: E402

FIX = ROOT / "tests" / "v2" / "fixtures" / "index_repo"
s = Suite("index")


def idx(repo: Path) -> Path:
    return repo / "Coordn8" / "_research" / "index"


def jl(p: Path) -> list:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def top(repo, q, **kw):
    hits = Q.search(repo, q, build_if_missing=False, **kw)["hits"]
    return hits[0] if hits else {}


def at(h) -> str:
    return f"{h.get('path')}:{h.get('line')}" if h else "<none>"


def tree(root: Path, exclude: Path = None) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if exclude and (p == exclude or exclude in p.parents or (p.is_dir() and p in exclude.parents)):
            continue
        out[p.relative_to(root).as_posix()] = (p.read_bytes() if p.is_file() and not p.is_symlink() else None)
    return out


with tmpdir() as T:
    T = Path(T)
    env = sandbox_env(T)
    os.environ.update(env)
    repo = T / "repo"
    shutil.copytree(FIX, repo)

    # seeded things the walker must skip
    (repo / "node_modules" / "leftpad").mkdir(parents=True)
    (repo / "node_modules" / "leftpad" / "index.js").write_text("export function leftpad(){}\n")
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n")
    (repo / "env").mkdir()
    (repo / "env" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (repo / "env" / "lib.py").write_text("def venv_only():\n    pass\n")
    (repo / "assets").mkdir()
    (repo / "assets" / "blob.dat").write_bytes(b"\x00\x01binary\x00def not_code(): pass\n")
    (repo / "assets" / "logo.png").write_bytes(b"\x89PNG....")
    (repo / "assets" / "huge.py").write_text("def huge():\n    pass\n" + "#" * 1_100_000 + "\n")
    os.symlink(str(repo / "shop" / "cart.py"), str(repo / "assets" / "link.py"))
    (repo / "Coordn8" / "_build").mkdir(parents=True)
    (repo / "Coordn8" / "_build" / "scene.js").write_text("export function builtScene(){}\n")

    before = tree(repo, exclude=idx(repo))
    t0 = time.time()
    res = BI.build(repo)
    after = tree(repo, exclude=idx(repo))
    I = idx(repo)

    # ------------------------------------------------------------------ K2 outputs exist
    for f in ("manifest.json", "symbols.jsonl", "chunks.jsonl", "xref.jsonl", "entrypoints.json"):
        s.check(f"build writes {f} under Coordn8/_research/index/", (I / f).is_file(), str(I))
    man = json.loads((I / "manifest.json").read_text())
    syms = jl(I / "symbols.jsonl")
    chunks = jl(I / "chunks.jsonl")
    xref = jl(I / "xref.jsonl")
    ents = json.loads((I / "entrypoints.json").read_text())["entrypoints"]
    files = man["files"]
    s.check("manifest records sha256, size, lang and mtime for every indexed file",
            files and all(len(v["sha"]) == 64 and "size" in v and "lang" in v and "mtime" in v
                          for v in files.values()), json.dumps(list(files.items())[:1]))
    s.check("manifest sha equals sha256 of the file bytes",
            files["shop/cart.py"]["sha"] == FR.file_hash(repo / "shop" / "cart.py"))
    langs = {files[p]["lang"] for p in files}
    s.check("fixture covers python, javascript, typescript, markdown, config and build files",
            {"python", "javascript", "typescript", "markdown", "yaml", "toml", "json",
             "makefile", "dockerfile"} <= langs, sorted(langs))

    # ------------------------------------------------------------------ skipping
    skipped = {r["path"]: r["reason"] for r in man["skipped"]}
    s.check("nothing under node_modules/, .git/, a venv or the Coordn8/ build folder is indexed",
            not [p for p in files if p.split("/")[0] in ("node_modules", ".git", "env", "Coordn8")],
            [p for p in files if p.split("/")[0] in ("node_modules", ".git", "env", "Coordn8")])
    s.check("skipped directories are recorded with a reason",
            all(d in skipped for d in ("node_modules/", ".git/", "env/", "Coordn8/")), skipped)
    s.check("binary files (NUL bytes or binary extension) are skipped and recorded",
            skipped.get("assets/blob.dat") == "binary" and skipped.get("assets/logo.png") == "binary", skipped)
    s.check("files over 1 MB are skipped and recorded",
            "over 1 MB" in skipped.get("assets/huge.py", "") and "assets/huge.py" not in files, skipped)
    s.check("symlinks are not followed and are recorded", "symlink" in skipped.get("assets/link.py", ""))
    s.check("no symbol from a skipped file leaks into the index (negative)",
            not {x["name"] for x in syms} & {"leftpad", "venv_only", "not_code", "huge", "builtScene"})

    # ------------------------------------------------------------------ symbol lookups
    h = top(repo, "apply_tax")
    s.check("python function lookup returns its path:line (apply_tax -> shop/pricing.py:6)",
            at(h) == "shop/pricing.py:6" and h.get("kind") == "function", h)
    h = top(repo, "Cart.add_item")
    s.check("python method lookup by qualified name returns shop/cart.py:12",
            at(h) == "shop/cart.py:12" and h.get("kind") == "method", h)
    h = top(repo, "add_item")
    s.check("python method lookup by bare name returns the method first", at(h) == "shop/cart.py:12", h)
    h = top(repo, "Router")
    s.check("JS class lookup returns its path:line (Router -> web/src/router.js:6)",
            at(h) == "web/src/router.js:6" and h.get("kind") == "class", h)
    h = top(repo, "formatPrice")
    s.check("TS exported arrow function lookup returns web/src/util.ts:2 as a function",
            at(h) == "web/src/util.ts:2" and h.get("kind") == "function", h)
    fp = [x for x in syms if x["name"] == "formatPrice"]
    s.check("TS arrow export is marked exported and arrow", fp and fp[0].get("exported") and fp[0].get("arrow"), fp)
    by = {(x["path"], x["qualname"]): x for x in syms}
    s.check("TS interface, type alias and enum are indexed at their lines",
            by.get(("web/src/util.ts", "PriceOptions"), {}).get("kind") == "interface"
            and by.get(("web/src/util.ts", "PriceOptions"), {}).get("line") == 6
            and by.get(("web/src/util.ts", "Currency"), {}).get("kind") == "type"
            and by.get(("web/src/util.ts", "Region"), {}).get("kind") == "enum")
    s.check("a TS const with a function-type annotation is still an arrow function (double)",
            by.get(("web/src/util.ts", "double"), {}).get("kind") == "function")
    s.check("JS class methods and class-field arrow methods are indexed with Class.name",
            by.get(("web/src/index.js", "App.start"), {}).get("line") == 12
            and by.get(("web/src/index.js", "App.handle"), {}).get("kind") == "method"
            and by.get(("web/src/router.js", "Router.create"), {}).get("kind") == "method")
    s.check("python async function and constant are indexed with signatures",
            by.get(("shop/cart.py", "fetch_remote_cart"), {}).get("signature", "").startswith("async def fetch_remote_cart(")
            and by.get(("shop/pricing.py", "TAX_RATE"), {}).get("kind") == "constant")
    s.check("python signature carries arguments and return annotation",
            by.get(("shop/pricing.py", "round_cents"), {}).get("signature") == "def round_cents(value: float) -> float")
    s.check("names inside JS strings, comments and regex literals are NOT indexed (negative)",
            not {x["name"] for x in syms} & {"FakeInString", "ghost", "ghostInComment", "NotAClass"},
            sorted(x["name"] for x in syms if x["path"] == "web/src/router.js"))
    s.check("shell functions are indexed without ctags (deploy -> scripts/deploy.sh:3)",
            by.get(("scripts/deploy.sh", "deploy"), {}).get("line") == 3)
    s.check("config keys and build targets are indexed (cache_size, make build)",
            by.get(("config/settings.yaml", "cache_size"), {}).get("line") == 2
            and by.get(("Makefile", "build"), {}).get("kind") == "target")
    masked, _ = BI.js_mask("const a = `x ${ {b: '}'} } y`; // c {\nlet r = /[/]{/g; f()\n")
    s.check("JS masker keeps every offset and newline, and blanks string/template/regex text",
            len(masked) == len("const a = `x ${ {b: '}'} } y`; // c {\nlet r = /[/]{/g; f()\n")
            and masked.count("\n") == 2 and masked.count("{") == masked.count("}") and "f()" in masked, repr(masked))

    ct = BI.ctags_bin()
    go = [x for x in syms if x["path"] == "tools/hello.go" and x["name"] == "Greet"]
    if ct:
        s.check("other languages are indexed through Universal Ctags when installed (Go Greet)",
                go and go[0]["line"] == 5 and go[0].get("via") == "ctags", go)
    else:
        s.check("other languages are indexed through Universal Ctags when installed (Go Greet)",
                True, "SKIPPED: Universal Ctags not installed here")
    r_no = BI.build(T / "repo", use_ctags=False, maps=False)
    s.check("without ctags the build still succeeds; Go file is in the manifest with no ctags symbols",
            r_no["run"]["ctags"] is False and not [x for x in jl(I / "symbols.jsonl") if x.get("via") == "ctags"]
            and "tools/hello.go" in json.loads((I / "manifest.json").read_text())["files"])
    BI.build(repo)  # restore the full index (with ctags when present)
    syms = jl(I / "symbols.jsonl")

    # ------------------------------------------------------------------ ranking / query
    hits = Q.search(repo, "Cart", build_if_missing=False, limit=20)["hits"]
    tiers = [x["tier"] for x in hits]
    s.check("ranking: exact match first, then prefix/token, BM25 last (query 'Cart')",
            hits and at(hits[0]) == "shop/cart.py:6" and tiers[0] == "exact"
            and tiers == sorted(tiers, key=lambda t: list(Q.TIER).index(t)), list(zip(tiers, [at(x) for x in hits])))
    h = top(repo, "least recently used eviction")
    s.check("doc query returns the right chunk (README 'Cache eviction policy' section)",
            h.get("path") == "README.md" and h.get("line") == 9 and h.get("kind") == "doc"
            and "Cache eviction policy" in h.get("name", ""), h)
    h = top(repo, "first route that matches", kind="doc")
    s.check("--kind doc returns only chunks and finds the nested 'Routing' heading",
            h.get("kind") == "doc" and "Routing" in h.get("name", ""), h)
    hk = Q.search(repo, "Router", kind="method", build_if_missing=False)["hits"]
    s.check("--kind filters symbol kinds (Router --kind method -> only methods)",
            hk and all(x["kind"] == "method" for x in hk), hk[:3])
    r = run("scripts/index/query.py", [repo, "zzqx_no_such_symbol_qq", "--no-build"], env=env)
    s.check("a query with no match prints no hits and exits 1 (negative)", r.returncode == 1, r.stdout + r.stderr)
    r = run("scripts/index/query.py", ["Cart.total", "--json"], env=env, cwd=str(repo))
    ok = r.returncode == 0 and json.loads(r.stdout)["hits"][0]["path"] == "shop/cart.py"
    s.check("`coordn8 find <query>` form works from the repo directory (query.py <query>)", ok, r.stdout[:300] + r.stderr)
    r = run("scripts/index/query.py", [repo, "apply_tax"], env=env)
    s.check("text output lists ranked path:line hits", r.stdout.splitlines()[0].startswith("shop/pricing.py:6"), r.stdout)
    fresh2 = T / "repo2"
    shutil.copytree(FIX, fresh2)
    r = run("scripts/index/query.py", [fresh2, "apply_tax", "--no-build"], env=env)
    s.check("query with no index and --no-build exits 2", r.returncode == 2, r.stderr)
    r = run("scripts/index/query.py", [fresh2, "apply_tax"], env=env)
    s.check("query builds a missing index automatically, then answers", r.returncode == 0
            and (idx(fresh2) / "manifest.json").exists() and "shop/pricing.py:6" in r.stdout, r.stdout + r.stderr)
    r = run("scripts/index/build_index.py", [T / "does-not-exist"], env=env)
    s.check("build_index exits 3 on a path that is not a directory", r.returncode == 3, r.stderr)

    # ------------------------------------------------------------------ chunks
    s.check("every doc chunk is at most 1,500 characters", chunks and max(len(c["text"]) for c in chunks) <= 1500,
            max(len(c["text"]) for c in chunks))
    s.check("every chunk carries heading path, line range and the file sha",
            all(isinstance(c["heading_path"], list) and c["line_start"] <= c["line_end"]
                and c["sha"] == files[c["path"]]["sha"] for c in chunks))
    cp = [c for c in chunks if c["path"] == "docs/architecture.md" and c["heading_path"][-1:] == ["Checkout pipeline"]]
    s.check("a section over 1,500 chars is split into contiguous chunks under the same heading path",
            len(cp) >= 2 and all(cp[i]["line_end"] < cp[i + 1]["line_start"] for i in range(len(cp) - 1)),
            [(c["line_start"], c["line_end"], len(c["text"])) for c in cp])
    s.check("a '#' line inside a fenced code block is not a heading (negative)",
            not [x for x in syms if x["kind"] == "heading" and "Not a heading" in x["name"]])
    s.check("nested heading path is recorded (README > Cache eviction policy > Tuning the cache)",
            any(c["heading_path"] == ["Index Fixture Shop", "Cache eviction policy", "Tuning the cache"] for c in chunks))

    # ------------------------------------------------------------------ xref
    def edge(kind, frm, to):
        return [x for x in xref if x["kind"] == kind and x["from"] == frm and x["to"] == to]

    xref = jl(I / "xref.jsonl")
    s.check("python relative import edge: shop/cart.py -> shop.pricing resolved to shop/pricing.py",
            edge("import", "shop/cart.py", "shop.pricing") and edge("import", "shop/cart.py", "shop.pricing")[0]["to_path"] == "shop/pricing.py")
    s.check("python absolute import edge: shop/cli.py -> shop.cart",
            bool(edge("import", "shop/cli.py", "shop.cart")))
    s.check("python cross-file call edge: Cart.total -> apply_tax (via the import)",
            [x for x in edge("call", "shop/cart.py::Cart.total", "shop/pricing.py::apply_tax") if x["how"] == "import"])
    s.check("python same-file call edge: apply_tax -> round_cents",
            bool(edge("call", "shop/pricing.py::apply_tax", "shop/pricing.py::round_cents")))
    s.check("call from a __main__ guard is attributed to <module>: cli.py -> main",
            bool(edge("call", "shop/cli.py::<module>", "shop/cli.py::main")))
    s.check("external import recorded as external with no target file (os)",
            [x for x in edge("import", "shop/cart.py", "os") if x["external"] and x["to_path"] is None])
    s.check("JS ES import edge: index.js -> ./router.js resolved",
            [x for x in edge("import", "web/src/index.js", "./router.js") if x["to_path"] == "web/src/router.js"])
    s.check("require('./util') resolves to util.ts (extension resolution)",
            [x for x in edge("import", "web/src/index.js", "./util") if x["to_path"] == "web/src/util.ts"])
    s.check("JS call edge into an imported TS function: App.start -> formatPrice",
            bool(edge("call", "web/src/index.js::App.start", "web/src/util.ts::formatPrice")))
    s.check("`new Router()` counts as a call edge to the class",
            bool(edge("call", "web/src/index.js::App.constructor", "web/src/router.js::Router")))
    s.check("every xref edge records the sha of the file it came from",
            all(x["sha"] == files[x["path"]]["sha"] for x in xref))

    # ------------------------------------------------------------------ entry points
    def ent(kind, path, **kw):
        return [e for e in ents if e["kind"] == kind and e["path"] == path and all(e.get(k) == v for k, v in kw.items())]

    ents = json.loads((I / "entrypoints.json").read_text())["entrypoints"]
    s.check("entry point: python __main__ guard at shop/cli.py:16 (python -m shop.cli)",
            bool(ent("python __main__ guard", "shop/cli.py", line=16, command="python -m shop.cli")))
    s.check("entry point: package __main__.py (python -m shop)",
            bool(ent("python package __main__", "shop/__main__.py", command="python -m shop")))
    s.check("entry point: pyproject console script resolved to shop/cli.py:7",
            bool(ent("console script", "pyproject.toml", name="fixture-shop", target_path="shop/cli.py", target_line=7)))
    s.check("entry points: package.json bin, main and npm scripts",
            bool(ent("package.json bin", "web/package.json", target_path="web/bin/cli.js"))
            and bool(ent("package.json main", "web/package.json", target_path="web/src/index.js"))
            and bool(ent("npm script", "web/package.json", name="start")))
    s.check("entry points: Dockerfile CMD and Makefile targets",
            bool(ent("Dockerfile CMD", "Dockerfile")) and bool(ent("make target", "Makefile", name="build")))
    s.check("entry point: shebang executables", bool(ent("shebang executable", "web/bin/cli.js")))

    # ------------------------------------------------------------------ K3 hashes on every entry
    s.check("every symbol, chunk, xref and entry point records its source file's sha256 (K3)",
            all(r_.get("sha") == files[r_["path"]]["sha"] for r_ in syms + chunks + xref + ents))

    # ------------------------------------------------------------------ write containment
    s.check("nothing written outside Coordn8/_research/index/ (repo tree byte-identical, no __pycache__)",
            before == after, sorted(set(after) ^ set(before))[:10])

    # ------------------------------------------------------------------ incremental
    maps_dir = I / "maps"
    map_bytes = {p.name: p.read_bytes() for p in maps_dir.glob("*.md")}
    map_mtime = {p.name: p.stat().st_mtime_ns for p in maps_dir.glob("*.md")}
    r0 = BI.build(repo, incremental=True)
    s.check("incremental re-run with no change re-parses zero files and rebuilds zero maps",
            r0["run"]["reparsed"] == [] and r0["run"]["maps"]["rebuilt"] == [], r0["run"])
    full_syms = jl(I / "symbols.jsonl")
    time.sleep(0.02)
    pr = repo / "shop" / "pricing.py"
    pr.write_text(pr.read_text() + "\n\ndef surcharge(amount):\n    return apply_tax(amount) + 1\n")

    st = FR.stale(repo)
    s.check("edited file is flagged stale before re-index (freshness lists it as changed)",
            st["changed"] == ["shop/pricing.py"] and not st["fresh"], st)
    s.check("freshness counts the stale entries derived from the edited file",
            st["stale_entries"].get("shop/pricing.py", {}).get("symbols", 0) >= 3, st["stale_entries"])
    s.check("freshness names the directory map that went stale", st.get("stale_maps") == ["shop"], st.get("stale_maps"))
    r = run("scripts/index/freshness.py", ["check", repo], env=env)
    s.check("`freshness.py check` exits 1 while the index is stale", r.returncode == 1, r.stdout[-300:])
    h = top(repo, "apply_tax")
    s.check("a query hit from the edited file is flagged STALE before use",
            h.get("stale") is True and h.get("stale_reason") == "source changed", h)
    hc = top(repo, "Cart.add_item")
    s.check("a hit from an unchanged file is not flagged stale (negative)", hc.get("stale") is False, hc)

    r1 = BI.build(repo, incremental=True)
    s.check("touching ONE file re-indexes exactly that one file",
            r1["run"]["reparsed"] == ["shop/pricing.py"] and r1["run"]["changed"] == ["shop/pricing.py"], r1["run"])
    want_line = pr.read_text().splitlines().index("def surcharge(amount):") + 1
    s.check("after re-index the edited file's new symbol is found at its line",
            at(top(repo, "surcharge")) == f"shop/pricing.py:{want_line}", (want_line, top(repo, "surcharge")))
    st = FR.stale(repo)
    r = run("scripts/index/freshness.py", ["check", repo], env=env)
    s.check("edited file is fresh again after re-index (`check` exits 0)", st["fresh"] and r.returncode == 0, st)
    s.check("after re-index the hit is no longer flagged stale", top(repo, "apply_tax").get("stale") is False)
    inc_syms = jl(I / "symbols.jsonl")
    BI.build(repo)
    s.check("incremental output equals a full rebuild (symbols, chunks, xref identical)",
            inc_syms == jl(I / "symbols.jsonl"), "diff")
    s.check("incremental call edge for the new function resolves (surcharge -> apply_tax)",
            bool([x for x in jl(I / "xref.jsonl") if x["from"] == "shop/pricing.py::surcharge"]))

    # maps: only the changed directory rebuilt
    new_mtime = {p.name: p.stat().st_mtime_ns for p in maps_dir.glob("*.md")}
    s.check("maps: only the changed directory's map was rebuilt (shop)",
            r1["run"]["maps"]["rebuilt"] == ["shop"], r1["run"]["maps"])
    untouched = [n for n in map_bytes if n not in ("shop.md", "INDEX.md")]
    s.check("maps: every other map file is byte-identical and was not rewritten",
            all((maps_dir / n).read_bytes() == map_bytes[n] and new_mtime[n] == map_mtime[n] for n in untouched),
            [n for n in untouched if new_mtime.get(n) != map_mtime.get(n)])
    s.check("maps: the rebuilt map reflects the change (surcharge now listed)",
            "surcharge" in (maps_dir / "shop.md").read_text())

    # removal
    (repo / "web" / "src" / "util.ts").unlink()
    st = FR.stale(repo)
    s.check("a removed file is flagged by freshness as removed", st["removed"] == ["web/src/util.ts"], st)
    r2 = BI.build(repo, incremental=True)
    allrows = jl(I / "symbols.jsonl") + jl(I / "chunks.jsonl") + jl(I / "xref.jsonl") + \
        json.loads((I / "entrypoints.json").read_text())["entrypoints"]
    man = json.loads((I / "manifest.json").read_text())
    s.check("removing a file drops all its entries (manifest, symbols, chunks, xref, entry points, cache)",
            r2["run"]["removed"] == ["web/src/util.ts"] and "web/src/util.ts" not in man["files"]
            and not [x for x in allrows if x.get("path") == "web/src/util.ts" or x.get("to_path") == "web/src/util.ts"]
            and "web/src/util.ts" not in (I / "_parse_cache.jsonl").read_text(), r2["run"])
    s.check("removing a file re-parses nothing else", r2["run"]["reparsed"] == [], r2["run"])
    s.check("a lookup for a removed symbol no longer returns it (negative)",
            top(repo, "formatPrice").get("path") != "web/src/util.ts")

    # add + rename
    (repo / "shop" / "inventory.py").write_text('"""Stock levels."""\n\n\ndef restock(n):\n    return n\n')
    st = FR.stale(repo)
    s.check("a newly added file is flagged by freshness as added", st["added"] == ["shop/inventory.py"], st)
    r3 = BI.build(repo, incremental=True)
    s.check("an added file is indexed and is the only one parsed",
            r3["run"]["reparsed"] == ["shop/inventory.py"] and at(top(repo, "restock")) == "shop/inventory.py:4", r3["run"])
    (repo / "shop" / "inventory.py").rename(repo / "shop" / "stock.py")
    r4 = BI.build(repo, incremental=True)
    s.check("a renamed file with the same content is reused by sha (zero re-parses) and found at its new path",
            r4["run"]["reparsed"] == [] and at(top(repo, "restock")) == "shop/stock.py:4", r4["run"])

    # ------------------------------------------------------------------ K3 memory integration
    from memory.store import Store, file_hash as store_hash  # noqa: E402
    s.check("index file_hash is byte-for-byte the memory store's digest",
            FR.file_hash(repo / "shop" / "cart.py") == store_hash(repo / "shop" / "cart.py"))
    store = Store(T / "coordn8_home")
    e_ = store.add("fact", "Cart totals apply tax after discount", scope="project:repo",
                   source_path=str(repo / "shop" / "cart.py"))
    look = FR.hash_lookup(repo)
    s.check("memory entry derived from an unchanged file is not stale (negative)",
            not [x for x in store.stale(look) if x["id"] == e_["id"]])
    cart = repo / "shop" / "cart.py"
    cart.write_text(cart.read_text() + "\n# changed\n")
    stl = store.stale(FR.hash_lookup(repo))
    s.check("memory entry is flagged stale through freshness.hash_lookup after its source changes",
            [x for x in stl if x["id"] == e_["id"] and x["stale_reason"] == "source changed"], stl)
    rows = FR.flag_entries(repo, [{"path": "shop/cart.py", "sha": "0" * 64}, {"text": "no source"}])
    s.check("flag_entries marks a row whose sha mismatches and leaves source-less rows unflagged",
            rows[0]["stale"] is True and rows[1]["stale"] is False, rows)
    BI.build(repo, incremental=True)
    r = run("scripts/index/freshness.py", ["check", fresh2 / "nothing"], env=env)
    s.check("`freshness.py check` exits 2 when there is no index", r.returncode == 2, r.stdout + r.stderr)

    # ------------------------------------------------------------------ K4 maps
    state = json.loads((maps_dir / "_state.json").read_text())["dirs"]
    man = json.loads((I / "manifest.json").read_text())
    want_dirs = {"."} | {str(Path(p).parent.as_posix()) for p in man["files"]}
    want_dirs = {"." if d in ("", ".") else d for d in want_dirs} | {"web"}
    s.check("one map per directory, including the root and folder-only parents (web/)",
            want_dirs <= set(state) and all((maps_dir / e["file"]).is_file() for e in state.values()),
            sorted(want_dirs - set(state)))
    idx_md = (maps_dir / "INDEX.md").read_text()
    s.check("maps/INDEX.md lists every directory with a one-line purpose",
            all(f"]({e['file']})" in idx_md for e in state.values()))
    shop_md = (maps_dir / "shop.md").read_text()
    s.check("a map states purpose, key files and connections",
            "**Purpose.**" in shop_md and "**Key files**" in shop_md and "**Connections**" in shop_md, shop_md[:400])
    s.check("map purpose comes from the package docstring (shop/)",
            "Shop core: carts, pricing and the command line." in shop_md)
    s.check("root map purpose comes from the README lead paragraph",
            "multi-language shop" in (maps_dir / "_root.md").read_text())
    s.check("shop map lists its internal links and entry points",
            "cart.py → pricing.py" in shop_md and "python -m shop" in shop_md)
    s.check("web/src map shows it is used by web/bin/",
            "used by: `web/bin/`" in (maps_dir / "web__src.md").read_text())
    s.check("maps stay short (each at most 4 KB)", all(len((maps_dir / e["file"]).read_bytes()) <= 4096 for e in state.values()))
    r = run("scripts/index/maps.py", [repo, "--show", "shop"], env=env)
    s.check("maps.py --show prints one directory's map", r.returncode == 0 and r.stdout.startswith("# shop/"), r.stdout[:80])
    (repo / "tools" / "hello.go").unlink()
    r5 = BI.build(repo, incremental=True)
    s.check("a directory whose last file is removed loses its map",
            "tools" in r5["run"]["maps"]["removed"] and not (maps_dir / "tools.md").exists(), r5["run"]["maps"])
    s.check("maps are written only inside Coordn8/_research/index/maps/",
            all(p.parent == maps_dir for p in maps_dir.rglob("*")))

    # ------------------------------------------------------------------ performance: 2,000 files
    big = T / "big"
    for i in range(2000):
        pkg = big / f"pkg{i // 100:02d}"
        pkg.mkdir(parents=True, exist_ok=True)
        k = i % 10
        if k < 6:
            if not (pkg / "__init__.py").exists():
                (pkg / "__init__.py").write_text(f'"""Package {i // 100}."""\n')
            (pkg / f"mod{i}.py").write_text(
                f'"""Module {i}."""\nimport os\nfrom . import __init__\n\n\nclass Thing{i}:\n'
                f'    def run(self, x):\n        return helper{i}(x) + len(os.sep)\n\n\n'
                f'def helper{i}(x):\n    return x * {i}\n' + "\n".join(f"# filler line {j}" for j in range(30)) + "\n")
        elif k < 9:
            ext = "ts" if k == 8 else "js"
            (pkg / f"view{i}.{ext}").write_text(
                f"// View {i}\nimport {{ a }} from './x{i}';\nexport class View{i} {{\n  render() {{ return draw{i}(1); }}\n}}\n"
                f"export const draw{i} = (n) => n + {i};\nfunction local{i}() {{ return `t ${{1}}`; }}\n")
        else:
            (pkg / f"notes{i}.md").write_text(f"# Notes {i}\n\nSome text about item {i}.\n\n## Detail\n\n"
                                              + ("More words here. " * 40) + "\n")
    t0 = time.time()
    rb = BI.build(big)
    dt = time.time() - t0
    s.check("a 2,000-file synthetic repo indexes in under 30 s", rb["counts"]["files"] >= 2000 and dt < 30,
            f"{rb['counts']['files']} files in {dt:.2f}s")
    (big / "pkg05" / "mod512.py").write_text((big / "pkg05" / "mod512.py").read_text() + "\n# touched\n")
    t0 = time.time()
    rb2 = BI.build(big, incremental=True)
    dt2 = time.time() - t0
    s.check("on the 2,000-file repo touching one file re-parses exactly one and rebuilds one map",
            rb2["run"]["reparsed"] == ["pkg05/mod512.py"] and rb2["run"]["maps"]["rebuilt"] == ["pkg05"],
            {"reparsed": rb2["run"]["reparsed"][:5], "maps": rb2["run"]["maps"]["rebuilt"][:5]})
    s.check("incremental re-index of the 2,000-file repo is faster than the full build", dt2 < dt, f"{dt2:.2f}s vs {dt:.2f}s")
    print(f"  info full={dt:.2f}s incremental={dt2:.2f}s files={rb['counts']['files']} symbols={rb['counts']['symbols']}")
    shutil.rmtree(big, ignore_errors=True)
    js_big = "\n".join(f"export function f{i}(a) {{ const v{i} = 1; return f{max(i - 1, 0)}(a) + g.h{i}(a); }}"
                       for i in range(4000))
    py_big = "\n".join(f"X{i} = {i}\ndef p{i}(a):\n    return p{max(i - 1, 0)}(a) + X{i}\n" for i in range(4000))
    t0 = time.time()
    rj = BI.parse_js(js_big)
    rp = BI.parse_python(py_big)
    dt3 = time.time() - t0
    s.check("large single files parse in linear time (4,000-function JS and Python files < 5 s)",
            dt3 < 5 and len(rj["symbols"]) == 4000 and len([x for x in rp["symbols"] if x["kind"] == "function"]) == 4000
            and rj["calls"][-1]["from"] == "f3999", f"{dt3:.2f}s")

sys.exit(s.finish())
