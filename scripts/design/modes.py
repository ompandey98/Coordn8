#!/usr/bin/env python3
"""Coordn8 — explain modes, dials and the topic stop condition (C5).

    python3 scripts/design/modes.py <page|diagram|video|answer> --repo REPO
        [--topic "how a refund is approved"] [--entry symbol,symbol] [--scope topic|all]
        [--detail low|medium|high] [--audience plain|mixed|field]
        [--content content.json]      page:    the page content (templates/page/README.md schema)
        [--desc desc.json]            diagram: the figure description (scripts/design/pick_diagram.py schema;
                                               every item carries `ref: "path:line"` or `inferred: true`)
        [--claims claims.json]        extra claims (page / diagram), the claims for the video sidecar
        [--design-system kit.json] [--slug NAME] [--out-dir REPO/Coordn8/...]

Modes
  page     the interactive page, ending with its claims ledger          -> Coordn8/<slug>.html (+ _build/)
  diagram  ONE figure and the claims behind it; hovering a node shows its path:line (an SVG <title>), and the
           figure can be read as a list that prints each path:line      -> Coordn8/<slug>.diagram.html
  video    the claims sidecar every video ships (the frames come from the video pipeline)
                                                                         -> Coordn8/<slug>.claims.md
  answer   chat only: prints the scoped answer plan as JSON and writes NO file at all

Dials are SEPARATE (reused from pick_diagram): `--detail` decides how many nodes are kept (low keeps half,
most important first) and never renames one; `--audience` decides what nodes are called and never drops one.

Stop condition: trace the topic end to end; do not explain the whole codebase unless asked. With `--topic`
or `--entry`, the scope is the files reachable from the topic's entry symbols (the files that define them,
then every in-repo module they import, transitively); every other source file is reported as skipped. The
whole codebase is in scope only with `--scope all`.

Writes: nothing outside <repo>/Coordn8/ — every path is resolved and refused if it leaves that folder
(symlinks included); answer mode refuses to write at all. One JSON line on stdout lists every file written.
Exit 0 done · 1 REVIEW (written, with leads) · 3 ERROR (nothing written). Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[1]
PKG = SCRIPTS.parent
for p in (str(SCRIPTS), str(SCRIPTS / "gates"), str(SCRIPTS / "render")):
    if p not in sys.path:
        sys.path.insert(0, p)

from design.pick_diagram import AUDIENCE_LEVELS, DETAIL_LEVELS, merge_plan, normalise, plan_figure  # noqa: E402

MODES = ("page", "diagram", "video", "answer")
OUT_DIRNAME = "Coordn8"
SRC_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}
SKIP_DIRS = {".git", "node_modules", OUT_DIRNAME, "__pycache__", ".venv", "venv", "env", "dist", "build",
             ".tox", ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "vendor", "site-packages"}
MAX_FILES = 6000
STOP_WORDS = {"the", "and", "for", "how", "does", "what", "why", "when", "with", "from", "into", "this", "that",
              "work", "works", "flow", "path", "are", "get", "gets", "its", "our", "your", "which", "who"}
EXIT_OK, EXIT_REVIEW, EXIT_ERROR = 0, 1, 3


class ModeError(Exception):
    pass


# ================================================================== the write guard (explain-06 / J2)
class Writer:
    """The only way this module writes. Every target must resolve inside <repo>/Coordn8/."""

    def __init__(self, repo: Path, mode: str):
        self.repo = repo.resolve()
        self.root = (self.repo / OUT_DIRNAME).resolve()
        self.mode = mode
        self.written: list = []

    def check(self, path: Path) -> Path:
        if self.mode == "answer":
            raise ModeError("answer mode is chat only: it writes no file")
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        # resolve the deepest existing parent so a symlinked folder cannot smuggle a write out
        probe = target
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        real = (probe.resolve() / target.relative_to(probe)) if probe != target else target.resolve()
        try:
            real.relative_to(self.root)
        except ValueError:
            raise ModeError(f"refused to write {target}: outside {self.root} (Coordn8 writes only into its own "
                            f"folder in the repo)")
        return real

    def write(self, path: Path, text: str) -> Path:
        real = self.check(path)
        real.parent.mkdir(parents=True, exist_ok=True)
        real.write_text(text, encoding="utf-8")
        self.written.append(str(real))
        return real


# ================================================================== scope: the topic stop condition
PY_DEF = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+([A-Za-z_]\w*)", re.M)
JS_DEF = re.compile(r"(?:^|[\s;])(?:export[ \t]+(?:default[ \t]+)?)?(?:async[ \t]+)?(?:function\*?|class)[ \t]+"
                    r"([A-Za-z_$][\w$]*)|(?:^|[\s;])(?:export[ \t]+)?(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*="
                    r"[ \t]*(?:async[ \t]*)?(?:function\b|\([^)]*\)[ \t]*=>|[A-Za-z_$][\w$]*[ \t]*=>)", re.M)
PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+\(?([^\n)#]+)", re.M)
PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w., \t]+)", re.M)
JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|^\s*import\s+)['"](\.{1,2}/[^'"]+)['"]""",
                       re.M)


def index_repo(repo: Path) -> list:
    out = []
    stack = [repo]
    while stack and len(out) < MAX_FILES:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for p in entries:
            if p.is_symlink():
                continue
            if p.is_dir():
                if p.name not in SKIP_DIRS and not p.name.startswith("."):
                    stack.append(p)
            elif p.suffix.lower() in SRC_EXT:
                out.append(p)
    return sorted(out)


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def definitions(files: list, repo: Path) -> dict:
    defs: dict = {}
    for f in files:
        text = _read(f)
        rx = PY_DEF if f.suffix == ".py" else JS_DEF
        for m in rx.finditer(text):
            name = next(g for g in m.groups() if g)
            line = text.count("\n", 0, m.start(m.lastindex or 1)) + 1
            defs.setdefault(name, []).append((f.relative_to(repo).as_posix(), line))
    return defs


def _py_module_file(mod: str, here: Path, repo: Path) -> list:
    if not mod:
        return []
    dots = len(mod) - len(mod.lstrip("."))
    name = mod.lstrip(".")
    bases = []
    if dots:
        base = here.parent
        for _ in range(dots - 1):
            base = base.parent
        bases = [base]
    else:
        bases = [repo, repo / "src", here.parent]
    out = []
    rel = Path(*name.split(".")) if name else Path()
    for b in bases:
        for cand in ((b / rel).with_suffix(".py") if name else None, b / rel / "__init__.py"):
            if cand is not None and cand.is_file():
                out.append(cand.resolve())
    return out


def imports_of(f: Path, repo: Path) -> list:
    text = _read(f)
    found = []
    if f.suffix == ".py":
        for m in PY_FROM.finditer(text):
            mod = m.group(1)
            found += _py_module_file(mod, f, repo)
            for nm in re.split(r"[,\s]+", m.group(2)):
                nm = nm.strip().split(" as ")[0]
                if re.fullmatch(r"\w+", nm or ""):
                    sub = (mod + "." + nm) if not mod.endswith(".") else (mod + nm)
                    found += _py_module_file(sub, f, repo)
        for m in PY_IMPORT.finditer(text):
            for mod in m.group(1).split(","):
                mod = mod.strip().split(" as ")[0].strip()
                found += _py_module_file(mod, f, repo)
    else:
        for m in JS_IMPORT.finditer(text):
            base = (f.parent / m.group(1))
            for cand in [base] + [Path(str(base) + e) for e in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")] + \
                    [base / ("index" + e) for e in (".js", ".ts", ".mjs")]:
                if cand.is_file():
                    found.append(cand.resolve())
                    break
    root = repo.resolve()
    return sorted({p for p in found if str(p).startswith(str(root)) and p != f.resolve()})


def _tokens(name: str) -> set:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return {t for t in re.split(r"[^a-zA-Z0-9]+", s.lower()) if t}


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w[:-1] if len(w) > 4 and w.endswith("e") else w


def trace_scope(repo: Path, topic: str | None, entries: list, scope: str) -> dict:
    repo = repo.resolve()
    files = index_repo(repo)
    rel = [f.relative_to(repo).as_posix() for f in files]
    if scope == "all":
        return {"scope": "all", "stop_condition": "the whole codebase was asked for (--scope all)",
                "entries": [], "files": rel, "skipped": [], "skipped_count": 0, "indexed": len(rel)}
    defs = definitions(files, repo)
    chosen_by = "named with --entry"
    if not entries and topic:
        words = {_stem(w) for w in re.findall(r"[a-z0-9]+", topic.lower()) if len(w) >= 3 and w not in STOP_WORDS}
        scored = []
        for name in defs:
            hit = len({_stem(t) for t in _tokens(name)} & words)
            if hit:
                scored.append((-hit, len(name), name))
        scored.sort()
        best = -scored[0][0] if scored else 0
        # the entry symbols are the ones that match the topic best (most topic words), at most five
        entries = [n for h, _, n in scored if -h == best][:5]
        chosen_by = f"matched from the topic's words (best match: {best} word(s))"
    if not entries:
        return {"scope": "topic", "status": "no-topic", "entries": [], "files": [], "skipped": rel[:50],
                "skipped_count": len(rel), "indexed": len(rel),
                "stop_condition": "no topic and no entry symbol was given: nothing is in scope. Give --topic or "
                                  "--entry; the whole codebase is explained only with --scope all"}
    start, ent, missing = [], [], []
    for e in entries:
        locs = defs.get(e)
        if not locs:
            missing.append(e)
            continue
        for path, line in locs:
            ent.append({"symbol": e, "ref": f"{path}:{line}"})
            start.append((repo / path).resolve())
    seen, queue, edges = set(), list(dict.fromkeys(start)), []
    while queue:
        f = queue.pop(0)
        if f in seen:
            continue
        seen.add(f)
        for g in imports_of(f, repo):
            edges.append([f.relative_to(repo).as_posix(), g.relative_to(repo).as_posix()])
            if g not in seen:
                queue.append(g)
    in_scope = sorted(p.relative_to(repo).as_posix() for p in seen)
    skipped = [r for r in rel if r not in set(in_scope)]
    return {"scope": "topic", "topic": topic, "entries": ent, "entries_chosen_by": chosen_by,
            "missing_entries": missing, "files": in_scope, "imports_followed": edges,
            "skipped": skipped[:200], "skipped_count": len(skipped), "indexed": len(rel),
            "stop_condition": "traced the topic end to end from its entry symbols through every in-repo import; "
                              "the files listed as skipped are not explained"}


# ================================================================== diagram mode: one figure + its claims
REF_RE = re.compile(r"^[^\s:]+(?:/[^\s:]+)*:\d+(?:-\d+)?$")


def _wrap(label: str, width: int = 22) -> list:
    words, lines, cur = str(label).split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines or [str(label)]


def figure_svg(title: str, nodes: list, edges: list) -> str:
    """A deterministic layered drawing: depth by longest path from the roots, one row per depth.
    Each node carries <title> (name and path:line) so hovering shows where it is in the code."""
    ids = [n["id"] for n in nodes]
    out_adj = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for a, b, _lab in edges:
        out_adj[a].append(b)
        indeg[b] += 1
    depth = {i: 0 for i in ids}
    order = [i for i in ids if indeg[i] == 0] or ids[:1]
    seen = set()
    queue = list(order)
    while queue:
        a = queue.pop(0)
        if a in seen:
            continue
        seen.add(a)
        for b in out_adj[a]:
            if b not in seen and depth[b] < depth[a] + 1:
                depth[b] = depth[a] + 1
            queue.append(b)
    rows: dict = {}
    for i in ids:
        rows.setdefault(depth[i], []).append(i)
    W, GAP_Y, PAD = 200, 70, 24
    lines = {n["id"]: _wrap(n["name"]) for n in nodes}
    heights = {i: 24 + 20 * len(lines[i]) for i in ids}
    widest = max(len(r) for r in rows.values())
    width = PAD * 2 + widest * W + (widest - 1) * 40
    pos, y = {}, PAD
    for d in sorted(rows):
        r = rows[d]
        row_w = len(r) * W + (len(r) - 1) * 40
        x0 = (width - row_w) / 2
        h = max(heights[i] for i in r)
        for k, i in enumerate(r):
            pos[i] = (x0 + k * (W + 40), y, W, heights[i])
        y += h + GAP_Y
    height = y - GAP_Y + PAD
    byid = {n["id"]: n for n in nodes}
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(width)} {int(height)}" width="{int(width)}" '
           f'height="{int(height)}"><title>{html.escape(title)}</title>',
           '<style>.c8-edge{marker-end:url(#c8m-arrow)}</style><defs><marker id="c8m-arrow" viewBox="0 0 10 10" refX="9" '
           'refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path class="c8-arrow" '
           'd="M0 0 L10 5 L0 10 z"/></marker></defs><g class="c8-edges">']
    labels = []
    for a, b, lab in edges:
        ax, ay, aw, ah = pos[a]
        bx, by, bw, bh = pos[b]
        x1, y1, x2, y2 = ax + aw / 2, ay + ah, bx + bw / 2, by
        if by <= ay:     # an edge back up the page: route it around the left side
            x1, y1, x2, y2 = ax, ay + ah / 2, bx, by + bh / 2
            mid = min(ax, bx) - 20
            svg.append(f'<path class="c8-edge" d="M{x1:.0f} {y1:.0f} L{mid:.0f} {y1:.0f} L{mid:.0f} {y2:.0f} L{x2:.0f} {y2:.0f}"/>')
        else:
            ym = (y1 + y2) / 2
            svg.append(f'<path class="c8-edge" d="M{x1:.0f} {y1:.0f} L{x1:.0f} {ym:.0f} L{x2:.0f} {ym:.0f} L{x2:.0f} {y2:.0f}"/>')
        if lab:
            lw = 14 + 7 * len(lab)
            lx, ly = ((x1 + x2) / 2, (y1 + y2) / 2)
            labels.append(f'<g class="c8-edge-label"><rect x="{lx - lw / 2:.0f}" y="{ly - 13:.0f}" width="{lw:.0f}" '
                          f'height="26" rx="13"/><text x="{lx:.0f}" y="{ly + 5:.0f}" text-anchor="middle">'
                          f'{html.escape(lab)}</text></g>')
    svg.append("</g>")
    svg += labels
    for i in ids:
        x, y0, w, h = pos[i]
        n = byid[i]
        where = n["ref"] if n.get("ref") else "inferred, no single line"
        attrs = f' data-node-id="{html.escape(i)}" data-ref="{html.escape(n.get("ref") or "")}"'
        if n.get("source"):
            attrs += f' data-source="{html.escape(n["source"])}"'
        if n.get("terminal"):
            attrs += ' data-kind="terminal"'
        txt = "".join(f'<text x="{x + w / 2:.0f}" y="{y0 + 30 + 20 * k:.0f}" text-anchor="middle">{html.escape(t)}</text>'
                      for k, t in enumerate(lines[i]))
        svg.append(f'<g class="c8-node"{attrs}><title>{html.escape(n["name"])} — {html.escape(where)}</title>'
                   f'<rect x="{x:.0f}" y="{y0:.0f}" width="{w:.0f}" height="{h:.0f}" rx="10"/>{txt}</g>')
    svg.append("</svg>")
    return "".join(svg)


def diagram_content(desc: dict, entry: dict, extra_claims: list) -> tuple:
    items, rels = normalise(desc)
    byid = {it["id"]: it for it in items}
    kept = entry["kept"]
    missing = [byid[k].get("label", k) for k in kept
               if not REF_RE.match(str(byid[k].get("ref") or "").strip()) and byid[k].get("inferred") is not True]
    if missing:
        raise ModeError("C3: these drawn nodes point to no path:line and are not marked inferred: "
                        + ", ".join(map(str, missing)) + " (read more, or set inferred: true on the item)")
    nodes, claims = [], []
    outdeg = {k: 0 for k in kept}
    for a, b in entry["edges_kept"]:
        outdeg[a] += 1
    for k in kept:
        it = byid[k]
        name = entry["names"][k]
        ref = str(it.get("ref") or "").strip()
        nodes.append({"id": k, "name": name, "ref": ref, "source": it.get("source"), "terminal": outdeg[k] == 0})
        claims.append({"id": f"n-{k}", "claim": str(it.get("claim") or f"{name} is part of this flow"),
                       "ref": ref, "status": "inferred" if (it.get("inferred") is True or not ref) else "read"})
    edges = []
    keptset = set(kept)
    for r in rels:
        if r["from"] in keptset and r["to"] in keptset:
            lab = str(r.get("label") or r.get("when") or "")
            edges.append((r["from"], r["to"], lab))
            if r.get("ref") and REF_RE.match(str(r["ref"]).strip()):
                claims.append({"id": f"e-{r['from']}-{r['to']}",
                               "claim": str(r.get("claim") or f"{entry['names'][r['from']]} leads to "
                                            f"{entry['names'][r['to']]}" + (f" when {lab}" if lab else "")),
                               "ref": str(r["ref"]).strip(), "status": "inferred" if r.get("inferred") else "read"})
    seen = {c["id"] for c in claims}
    for c in extra_claims:
        if isinstance(c, dict) and str(c.get("id")) not in seen:
            claims.append(c)
    title = str(desc.get("title") or entry["id"])
    svg = figure_svg(title, nodes, edges)
    graph = {"nodes": [{"id": n["id"], "label": n["name"], "ref": n["ref"] or None} for n in nodes],
             "edges": [{"from": a, "to": b, "label": lab} for a, b, lab in edges]}
    cut = "; ".join(entry.get("cuts") or [])
    content = {
        "title": title,
        "conclusion": str(desc.get("conclusion") or desc.get("text") or title),
        "sections": [{"id": entry["id"], "title": title,
                      "summary": str(desc.get("summary") or "Hover a box to see the file and line it comes from."),
                      "components": [{"type": "flow", "id": f"{entry['id']}-figure", "title": title, "svg": svg,
                                      "graph": graph, "explains": str(desc.get("text") or title),
                                      "caption": ("Hover a box for its file and line, or read the figure as a list."
                                                  + (f" Left out at this level of detail: {cut}." if cut else ""))}]}],
        "claims": claims, "claims_title": "Where each part of the figure comes from"}
    return content, nodes, edges


# ================================================================== the modes
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-") or "explain"


def _load_json(p, what: str):
    if not p:
        return None
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as ex:
        raise ModeError(f"cannot read {what} {p}: {ex}")


def _claims_list(data) -> list:
    if data is None:
        return []
    rows = data.get("claims") if isinstance(data, dict) else data
    return [r for r in rows or [] if isinstance(r, dict)]


def _default_kit() -> Path:
    for name in ("quiet-ward.json",):
        p = PKG / "data" / "style_kits" / name
        if p.exists():
            return p
    kits = sorted((PKG / "data" / "style_kits").glob("*.json"))
    if not kits:
        raise ModeError("no style kit found under data/style_kits")
    return kits[0]


def _build_page(content: dict, content_dir: Path, design: Path, out_html: Path, w: Writer, build_dir: Path) -> dict:
    import build_page as bp
    w.check(out_html)
    try:
        page, summary, plan, claims = bp.build(content, content_dir, design, None, out_html.parent)
    except bp.BuildError as ex:
        raise ModeError(f"page build refused: {ex}")
    w.write(out_html, page)
    manifest = {"claims": [{"id": c["id"], "text": c["text"], "ref": c["ref"], "status": c["status"], "kind": "claim"}
                           for c in claims]}
    w.write(build_dir / (out_html.stem + ".claims.json"), json.dumps(manifest, indent=1, ensure_ascii=False))
    w.write(build_dir / (out_html.stem + ".page_plan.json"), json.dumps(plan, indent=1, ensure_ascii=False))
    return {"summary": summary, "claims": claims, "figures": page.count('class="c8-flow"')}


def run(a) -> tuple:
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        raise ModeError(f"--repo {a.repo} is not a directory")
    w = Writer(repo, a.mode)
    if a.mode == "answer" and a.out_dir:
        raise ModeError("answer mode is chat only: --out-dir is not accepted")
    out_dir = w.check(Path(a.out_dir)) if a.out_dir else (None if a.mode == "answer" else w.check(w.root))
    build_dir = (out_dir / "_build") if out_dir else None
    slug = _slug(a.slug or repo.name)
    entries = [e.strip() for e in (a.entry or "").split(",") if e.strip()]
    scope = trace_scope(repo, a.topic, entries, a.scope) if (a.topic or entries or a.scope == "all") else {
        "scope": a.scope, "status": "not-traced", "files": [], "skipped_count": 0,
        "stop_condition": "no topic given: the deliverable's own content defines what is explained"}
    dials = {"detail": a.detail, "audience": a.audience,
             "detail_controls": "how many nodes are kept (never what they are called)",
             "audience_controls": "what nodes are called (never how many are kept)"}
    res = {"tool": "modes", "mode": a.mode, "status": "PASS", "repo": str(repo), "scope": scope, "dials": dials}

    if a.mode == "answer":
        res.update({"writes": [], "deliver": "chat",
                    "answer_plan": {"open_with": "the conclusion, in one or two sentences",
                                    "then": "trace the topic once, end to end, through the files in scope, naming "
                                            "each piece by what it does",
                                    "cite": "every claim with its path:line, or say it is inferred",
                                    "end_with": "what the reader can now do",
                                    "files_to_read": scope.get("files", [])[:60]},
                    "summary": "answer mode: chat only, no file written"})
        return res, EXIT_OK

    extra = _claims_list(_load_json(a.claims, "claims"))
    design = Path(a.design_system) if a.design_system else _default_kit()

    if a.mode == "page":
        content = _load_json(a.content, "content")
        if not isinstance(content, dict):
            raise ModeError("page mode needs --content (the page content JSON)")
        if extra:
            content = dict(content)
            content["claims"] = list(content.get("claims") or []) + extra
        out = _build_page(content, Path(a.content).resolve().parent, design, out_dir / f"{slug}.html", w, build_dir)
        res.update({"figures": out["figures"], "claims": len(out["claims"]),
                    "inferred": sum(1 for c in out["claims"] if c["status"] == "inferred"),
                    "warnings": out["summary"]["warnings"]})
    elif a.mode == "diagram":
        desc = _load_json(a.desc, "figure description")
        if not isinstance(desc, dict):
            raise ModeError("diagram mode needs --desc (a figure description; see scripts/design/pick_diagram.py)")
        entry = plan_figure(desc, detail=a.detail, audience=a.audience)
        content, nodes, edges = diagram_content(desc, entry, extra)
        fig = _slug(entry["id"])
        out = _build_page(content, Path(a.desc).resolve().parent, design, out_dir / f"{slug}.{fig}.diagram.html",
                          w, build_dir)
        w.check(build_dir / "diagram_plan.json")
        w.written.append(str(merge_plan(build_dir, entry)))
        res.update({"figures": out["figures"], "figure": entry["id"], "type": entry["type"],
                    "nodes": entry["nodes"], "source_nodes": entry["source_nodes"], "names": entry["names"],
                    "kept": entry["kept"], "cuts": entry["cuts"], "claims": len(out["claims"]),
                    "inferred": sum(1 for c in out["claims"] if c["status"] == "inferred")})
    elif a.mode == "video":
        import check_claims as cc
        if not extra:
            raise ModeError("video mode needs --claims: every video ships <slug>.claims.md")
        rows = []
        for i, c in enumerate(extra, 1):
            rows.append({"id": str(c.get("id") or f"c{i}"), "text": str(c.get("text") or c.get("claim") or ""),
                         "ref": str(c.get("ref") or "").strip(), "basis": cc.basis(c)})
        bad = [r["id"] for r in rows if r["basis"] == "unmarked"]
        if bad:
            raise ModeError(f"C3: claim(s) {bad} point to no path:line and are not marked inferred; no sidecar written")
        side = w.write(out_dir / f"{slug}.claims.md", cc.render_md(rows, slug))
        w.write(build_dir / "mode.json", json.dumps({"mode": "video", "slug": slug, "scope": scope, "dials": dials,
                                                     "claims_sidecar": str(side)}, indent=1))
        res.update({"claims": len(rows), "inferred": sum(1 for r in rows if r["basis"] == "inferred"),
                    "next": "render the frames with the video pipeline; the sidecar ships beside <slug>.mp4"})
    res["writes"] = w.written
    if res.get("warnings"):
        res["status"] = "REVIEW"
    res["summary"] = f"{a.mode} mode: wrote {len(w.written)} file(s), all inside {w.root}"
    return res, (EXIT_REVIEW if res.get("warnings") else EXIT_OK)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 explain modes (C5).")
    ap.add_argument("mode", choices=MODES)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--topic")
    ap.add_argument("--entry", help="comma list of entry symbols (functions, classes)")
    ap.add_argument("--scope", choices=("topic", "all"), default="topic")
    ap.add_argument("--detail", choices=DETAIL_LEVELS, default="medium")
    ap.add_argument("--audience", choices=AUDIENCE_LEVELS, default="plain")
    ap.add_argument("--content")
    ap.add_argument("--desc")
    ap.add_argument("--claims")
    ap.add_argument("--design-system")
    ap.add_argument("--slug")
    ap.add_argument("--out-dir")
    a = ap.parse_args(argv)
    try:
        res, rc = run(a)
    except ModeError as ex:
        print(json.dumps({"tool": "modes", "mode": a.mode, "status": "ERROR", "writes": [], "summary": str(ex)}))
        return EXIT_ERROR
    print(json.dumps(res, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
