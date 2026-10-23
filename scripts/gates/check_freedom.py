#!/usr/bin/env python3
"""
Coordn8 gate — FREEDOM DOCTRINE AUDIT (H1): no gate may fail something only because it is
not listed in a catalog.

Option catalogs (palettes, style kits, diagram types, scene types, caption styles, page
components, audiences, explanation shapes, idea techniques, voices — every data file that calls
itself a menu / starting point / never a fence, plus the kit and page-component folders) are
MENUS. A gate may READ them — for defaults, budgets, wording — but its pass/fail may never
depend on whether an id is a member of one. This audit proves that statically for every gate
that exists when it runs (it globs, so gates added later are covered the moment they land).
Reference data that is not a menu of options (a measurement lexicon, a fixed failure taxonomy,
an agent registry) is listed in the report as such and is not a taint source; `--all-data`
treats every data file as a catalog.

HOW (pure static analysis with the standard-library `ast`; no gate is executed):
  1. sources     a string constant (or a `/`-joined path of constants) naming a catalog file or
                 folder taints the expression it sits in;
  2. data flow   labels flow through assignments (tuple-wise), for-loops, comprehensions (with
                 their own scope), function returns (position-wise, context-sensitive: a helper
                 that only returns what it was given is tainted only where it was given a
                 catalog), parameters that receive a catalog at a call site, and functions and
                 names imported from other package modules (one level, cached);
                 barriers: booleans (comparisons, `not`), arithmetic and len/int/max/… — a budget
                 read from a catalog is a value, not a list of ids;
  3. membership  `in` / `not in` against a catalog-derived collection, `<catalog>.get(x)`,
                 a None/truth test of `next(… for e in <catalog> if …== x)`, `any(…)` over a catalog
                 with an equality, a variable holding any of these, a helper returning any of
                 these — each with its polarity (true = listed);
  4. sinks       what makes a gate fail: adding to a findings-like container (named findings /
                 problems / errors / issues / failures / violations / bad / hits…, stored under such
                 a key, tested to choose a REVIEW/FAIL status, passed to or returned from a helper
                 that is used so), raise, assert, `sys.exit(non-zero)`, returning EXIT_REVIEW /
                 EXIT_ERROR, writing a REVIEW / FAIL / BLOCK / REJECT status, `ok = False`;
  5. verdict     a gate FAILS when the branch taken for a NOT-LISTED value reaches a sink (the
                 else of `if x in cat`, the body of `if x not in cat`, an assert on membership, a
                 comprehension that keeps not-listed items and feeds findings), or when it hands a
                 catalog to an imported helper that does so. Requiring an option to be DECLARED
                 invented in order to pass is still a fence and still fails.

Every gate gets a line saying why it is clean: it reads no catalog; it reads which catalogs for
values only; or it tests membership only to choose defaults (lines given) and the not-listed
branch adds no finding.

Usage:
    python3 scripts/gates/check_freedom.py                 # every gate in the package
    python3 scripts/gates/check_freedom.py path/a.py dir/  # specific files / directories
        [--data DIR] [--all-data]
Default targets: scripts/gates/*.py, scripts/review/*.py and scripts/design/divergence.py,
whatever exists at run time.
Exit: 0 PASS · 1 FAIL (a gate fences on catalog membership) · 2 SKIPPED (no gates) ·
3 ERROR (a gate could not be parsed and nothing failed).
Zero third-party deps; Python 3.9+.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PKG_ROOT / "scripts"
GATE = "freedom"
CAT = "CAT"      # a value read out of a catalog (entries, budgets, word lists …)
IDS = "IDS"      # a collection of catalog entry IDS — the only thing membership in which can fence
ID_KEYS = {"id", "name", "family", "slug", "type", "kind", "key"}

MENU_WORDS = re.compile(r"never\s+a?\s*fences?|starting\s+points?|\ba\s+menu\b|\bmenus?\s+of\b", re.I)
FOLDER_CATALOGS = ("style_kits", "page/components")

SINK_TOKENS = {"finding", "findings", "problem", "problems", "error", "errors", "errs", "err", "issue",
               "issues", "failure", "failures", "fails", "failed", "violation", "violations", "bad",
               "rejected", "rejects", "rejection", "rejections", "defect", "defects", "hits", "blockers",
               "blocking", "fences", "fenced", "over", "complaints"}
SINK_KEYS = {"findings", "problems", "errors", "issues", "failures", "violations", "blocking", "rejected",
             "defects", "fails", "failed", "bad"}
FAIL_WORDS = {"REVIEW", "FAIL", "FAILED", "BLOCK", "BLOCKED", "REJECT", "REJECTED", "ERROR"}
STATUS_KEYS = {"status", "verdict", "result", "outcome"}
EXIT_FAIL_NAMES = {"EXIT_REVIEW", "EXIT_ERROR", "EXIT_FAIL", "EXIT_BLOCK"}
MUTATORS = {"append", "extend", "add", "insert", "update", "appendleft"}
SCALAR_CALLS = {"len", "int", "float", "round", "abs", "max", "min", "sum", "bool", "str", "range",
                "isinstance", "hasattr", "callable", "id", "hash", "repr", "print", "format", "ord", "chr",
                "type", "issubclass"}
SCALAR_METHODS = {"lower", "upper", "strip", "lstrip", "rstrip", "startswith", "endswith", "count", "find",
                  "format", "isdigit", "isalpha", "join", "exists", "is_file", "is_dir"}
FALSEY_FLAGS = re.compile(r"^(ok|passed|valid|is_valid|pass_|good|allowed|accepted)$")
COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


# ------------------------------------------------------------------ catalog sources
def classify_data(data_dir: Path, all_data: bool = False) -> Tuple[List[str], Dict[str, str]]:
    """(catalog source suffixes, {reference-data file: why it is not a menu})."""
    sources: List[str] = list(FOLDER_CATALOGS)
    reference: Dict[str, str] = {}
    if data_dir.is_dir():
        for p in sorted(data_dir.iterdir()):
            if p.name.startswith(".") or p.name.lower() == "readme.md":
                continue
            if p.is_dir():
                if p.name not in sources:
                    sources.append(p.name)
                continue
            if p.suffix.lower() != ".json":
                continue
            if all_data:
                sources.append(p.name)
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                sources.append(p.name)
                continue
            text = " ".join(str(d.get(k, "")) for k in ("about", "doctrine")) if isinstance(d, dict) else ""
            if MENU_WORDS.search(text):
                sources.append(p.name)
            else:
                reference[p.name] = ("reference data, not a menu of options (it does not call itself a "
                                     "starting point / never a fence); membership in it may be measured")
    return sources, reference


def _norm(v: str) -> str:
    return v.replace("\\", "/").strip().rstrip("/")


def _matches(path: str, sources: List[str]) -> Optional[str]:
    path = _norm(path)
    if not path or len(path) > 300 or "\n" in path:
        return None
    for s in sources:
        if path == s or path.endswith("/" + s):
            return s
    return None


def _div_chain(e: ast.AST) -> Optional[str]:
    """`ROOT / "templates" / "page" / "components"` -> 'templates/page/components'."""
    parts = []
    while isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
        if isinstance(e.right, ast.Constant) and isinstance(e.right.value, str):
            parts.append(e.right.value)
        else:
            return None
        e = e.left
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        parts.append(e.value)
    return "/".join(reversed(parts)) if parts else None


def _id_like(e: ast.AST) -> bool:
    """`t["id"]`, `t.get("name")`, `p.stem`, possibly wrapped in str()/.lower()/.strip()."""
    while True:
        if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id == "str" and e.args:
            e = e.args[0]
        elif isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and \
                e.func.attr in ("lower", "upper", "strip", "casefold") and not e.args:
            e = e.func.value
        else:
            break
    if isinstance(e, ast.Subscript):
        sl = e.slice.value if isinstance(e.slice, ast.Index) else e.slice  # type: ignore[attr-defined]
        return isinstance(sl, ast.Constant) and sl.value in ID_KEYS
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "get" and e.args:
        return isinstance(e.args[0], ast.Constant) and e.args[0].value in ID_KEYS
    if isinstance(e, ast.Attribute):
        return e.attr in ("stem", "name")
    return False


def _tokens(name: str) -> Set[str]:
    return {t for t in re.split(r"[_\W]+", name.lower()) if t}


def _root_name(node: ast.AST) -> Optional[str]:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred)):
        node = node.value
    if isinstance(node, ast.Call):
        return _root_name(node.func) if isinstance(node.func, ast.Attribute) else None
    return node.id if isinstance(node, ast.Name) else None


def _target_names(t: ast.AST) -> List[Tuple[Optional[int], str]]:
    if isinstance(t, ast.Name):
        return [(None, t.id)]
    if isinstance(t, ast.Starred):
        return _target_names(t.value)
    if isinstance(t, (ast.Tuple, ast.List)):
        out = []
        for i, e in enumerate(t.elts):
            out += [(i, n) for _, n in _target_names(e)]
        return out
    return []


def _seg(src: str, node: ast.AST) -> str:
    try:
        s = ast.get_source_segment(src, node) or ""
    except Exception:  # noqa: BLE001
        s = ""
    return re.sub(r"\s+", " ", s).strip()[:140]


# ------------------------------------------------------------------ module analysis
class ModuleInfo:
    """Labels (catalog data flow), membership flags and sinks for one source file, to a fixpoint."""

    def __init__(self, path: Path, src: str, tree: ast.Module, sources: List[str], resolver):
        self.path, self.src, self.tree, self.sources, self.resolver = path, src, tree, sources, resolver
        self.nodes: List[Tuple[ast.AST, Tuple]] = []
        self.funcs: Dict[str, ast.AST] = {}
        self.params: Dict[int, List[str]] = {}
        self.bound: Dict[int, Set[str]] = {0: set()}
        self.labels: Dict[int, Dict[str, Set]] = {}
        self.flags: Dict[int, Dict[str, Set[int]]] = {}
        self.sinks: Dict[int, Set[str]] = {}
        self.ret: Dict[str, Dict[Optional[int], Set]] = {}
        self.member_funcs: Dict[str, Set[int]] = {}
        self.sink_funcs: Dict[str, Set[Optional[int]]] = {}
        self.receives_cat: Set[Tuple[int, int]] = set()    # params handed catalog ID collections
        self.receives_data: Set[Tuple[int, int]] = set()   # params handed any catalog data
        self.imports: Dict[str, Tuple["ModuleInfo", Optional[str]]] = {}
        self.used_sources: Set[str] = set()
        self._walk(tree, ())
        for fid, ps in self.params.items():
            d = self.labels.setdefault(fid, {})
            for i, p in enumerate(ps):
                d.setdefault(p, set()).add(("p", fid, i))
        self._resolve_imports()
        self._fixpoint()

    # ---- structure and scopes
    @staticmethod
    def _bind_key(chain: Tuple) -> int:
        """Assignments bind in the innermost function (comprehensions only bind their targets)."""
        for n in reversed(chain):
            if isinstance(n, FUNCS):
                return id(n)
        return 0

    def _bind(self, chain: Tuple, target: ast.AST):
        k = self._bind_key(chain)
        for _, n in _target_names(target):
            self.bound.setdefault(k, set()).add(n)

    def _walk(self, node: ast.AST, chain: Tuple):
        self.nodes.append((node, chain))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if not isinstance(node, ast.Lambda):
                self.funcs[node.name] = node
                self.bound.setdefault(self._bind_key(chain), set()).add(node.name)
                for d in node.decorator_list:
                    self._walk(d, chain)
            a = node.args
            ps = [x.arg for x in a.posonlyargs + a.args] + ([a.vararg.arg] if a.vararg else []) + \
                [x.arg for x in a.kwonlyargs] + ([a.kwarg.arg] if a.kwarg else [])
            self.params[id(node)] = ps
            self.bound.setdefault(id(node), set()).update(ps)
            for d in a.defaults + [d for d in a.kw_defaults if d is not None]:
                self._walk(d, chain)
            inner = chain + (node,)
            for st in (node.body if isinstance(node.body, list) else [node.body]):
                self._walk(st, inner)
            return
        if isinstance(node, COMPS):
            inner = chain + (node,)
            self.bound.setdefault(id(node), set())
            for g in node.generators:
                for _, n in _target_names(g.target):
                    self.bound[id(node)].add(n)
            for c in ast.iter_child_nodes(node):
                self._walk(c, inner)
            return
        if isinstance(node, (ast.Assign,)):
            for t in node.targets:
                self._bind(chain, t)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            self._bind(chain, node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._bind(chain, node.target)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            self._bind(chain, node.optional_vars)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            self.bound.setdefault(self._bind_key(chain), set()).add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                self.bound.setdefault(self._bind_key(chain), set()).add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.NamedExpr):
            self._bind(tuple(n for n in chain if not isinstance(n, COMPS)), node.target)
        for c in ast.iter_child_nodes(node):
            self._walk(c, chain)

    def _scope_of_name(self, name: str, chain: Tuple) -> Optional[int]:
        for i in range(len(chain), -1, -1):
            k = id(chain[i - 1]) if i else 0
            if name in self.bound.get(k, ()):
                return k
        return None

    def _get(self, store: Dict[int, dict], name: str, chain: Tuple, default):
        k = self._scope_of_name(name, chain)
        if k is None:
            return default
        return store.get(k, {}).get(name, default)

    def _store_key(self, name: str, chain: Tuple) -> int:
        k = self._scope_of_name(name, chain)
        return self._bind_key(chain) if k is None else k

    @staticmethod
    def _enclosing_def(chain: Tuple):
        for f in reversed(chain):
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return f
        return None

    def _enclosing_func(self, chain: Tuple) -> Optional[str]:
        f = self._enclosing_def(chain)
        return f.name if f is not None else None

    def _resolve_imports(self):
        for node, _ in self.nodes:
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mod = self.resolver(node.module)
                if mod is None:
                    continue
                for a in node.names:
                    sub = self.resolver(f"{node.module}.{a.name}")
                    self.imports[a.asname or a.name] = (sub, None) if sub is not None else (mod, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    mod = self.resolver(a.name)
                    if mod is not None:
                        self.imports[a.asname or a.name.split(".")[0]] = (mod, None)

    # ---- labels
    def is_cat(self, labels: Set) -> bool:
        for lab in labels:
            if lab == IDS:
                return True
            if isinstance(lab, tuple) and (lab[1], lab[2]) in self.receives_cat:
                return True
        return False

    def has_data(self, labels: Set) -> bool:
        for lab in labels:
            if lab in (CAT, IDS):
                return True
            if isinstance(lab, tuple) and (lab[1], lab[2]) in self.receives_data:
                return True
        return False

    def _call_target(self, func: ast.AST) -> Tuple[Optional["ModuleInfo"], Optional[str]]:
        if isinstance(func, ast.Name):
            if func.id in self.imports:
                m, attr = self.imports[func.id]
                return (m, attr) if attr else (None, None)
            if func.id in self.funcs and self._scope_of_name(func.id, ()) is not None:
                return self, func.id
            if func.id in self.funcs:
                return self, func.id
            return None, None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in self.imports:
            m, attr = self.imports[func.value.id]
            if attr is None:
                return m, func.attr
        return None, None

    def _arg_for(self, call: ast.Call, fdef, idx: int) -> Optional[ast.AST]:
        ps = self.params_of(fdef)
        if idx < len(call.args) and not any(isinstance(a, ast.Starred) for a in call.args[:idx + 1]):
            return call.args[idx]
        if idx < len(ps):
            for kw in call.keywords:
                if kw.arg == ps[idx]:
                    return kw.value
        return None

    @staticmethod
    def params_of(fdef) -> List[str]:
        a = fdef.args
        return [x.arg for x in a.posonlyargs + a.args] + ([a.vararg.arg] if a.vararg else []) + \
            [x.arg for x in a.kwonlyargs] + ([a.kwarg.arg] if a.kwarg else [])

    def _subst(self, labels: Set, call: ast.Call, m: "ModuleInfo", fdef, chain: Tuple) -> Set:
        out: Set = set()
        for lab in labels:
            if lab in (CAT, IDS):
                out.add(lab)
            elif isinstance(lab, tuple) and lab[1] == id(fdef):
                arg = self._arg_for(call, fdef, lab[2])
                if arg is not None:
                    out |= self.labels_of(arg, chain)
            elif m is self:
                out.add(lab)
        return out

    def call_labels(self, call: ast.Call, chain: Tuple) -> Optional[Dict[Optional[int], Set]]:
        m, fname = self._call_target(call.func)
        if m is None or not fname or fname not in m.funcs:
            return None
        fdef = m.funcs[fname]
        rets = m.ret.get(fname, {})
        out = {pos: self._subst(lab, call, m, fdef, chain) for pos, lab in rets.items()}
        if m is not self and any(CAT in v for v in out.values()):
            self.used_sources |= m.used_sources
        return out

    def labels_of(self, e: Optional[ast.AST], chain: Tuple) -> Set:
        if e is None:
            return set()
        if isinstance(e, ast.Constant):
            if isinstance(e.value, str):
                hit = _matches(e.value, self.sources)
                if hit:
                    self.used_sources.add(hit)
                    return {CAT}
            return set()
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Div):
            joined = _div_chain(e)
            if joined:
                hit = _matches(joined, self.sources)
                if hit:
                    self.used_sources.add(hit)
                    return {CAT}
        if isinstance(e, (ast.Compare, ast.Lambda)):
            return set()
        if isinstance(e, ast.UnaryOp):
            return set() if isinstance(e.op, ast.Not) else self.labels_of(e.operand, chain)
        if isinstance(e, ast.BinOp) and not isinstance(e.op, (ast.Div, ast.Add, ast.BitOr)):
            return set()
        if isinstance(e, ast.Name):
            if not isinstance(e.ctx, ast.Load):
                return set()
            if self._scope_of_name(e.id, chain) is None or e.id in self.imports:
                if e.id in self.imports:
                    m, attr = self.imports[e.id]
                    if attr and CAT in m.labels.get(0, {}).get(attr, set()):
                        self.used_sources |= m.used_sources
                        return {CAT}
                return set()
            return set(self._get(self.labels, e.id, chain, set()))
        if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and e.value.id in self.imports:
            m, attr = self.imports[e.value.id]
            if attr is None:
                if CAT in m.labels.get(0, {}).get(e.attr, set()):
                    self.used_sources |= m.used_sources
                    return {CAT}
                return set()
        if isinstance(e, ast.Subscript):
            # an entry taken out of an id-keyed collection is an entry, not a list of ids
            base = self.labels_of(e.value, chain)
            return (base - {IDS}) if not isinstance(e.slice, ast.Slice) else base
        if isinstance(e, ast.Call):
            f = e.func
            if isinstance(f, ast.Attribute) and f.attr in ("get", "values", "items", "pop", "setdefault"):
                out = self.labels_of(f.value, chain) - {IDS}
                for a in e.args[1:]:
                    out |= self.labels_of(a, chain)
                return out
            if isinstance(f, ast.Name) and f.id in SCALAR_CALLS:
                return set()
            if isinstance(f, ast.Attribute) and (f.attr in SCALAR_METHODS or
                                                 (isinstance(f.value, ast.Name) and f.value.id == "math")):
                return set()
            cl = self.call_labels(e, chain)
            if cl is not None:
                out: Set = set()
                for v in cl.values():
                    out |= v
                return out
        inner = chain + (e,) if isinstance(e, COMPS) else chain
        out = set()
        for c in ast.iter_child_nodes(e):
            if isinstance(c, ast.comprehension):
                out |= self.labels_of(c.iter, inner)
            elif isinstance(c, (ast.expr, ast.keyword)):
                out |= self.labels_of(c.value if isinstance(c, ast.keyword) else c, inner)
        if isinstance(e, COMPS) and self.has_data(out):
            elt = e.key if isinstance(e, ast.DictComp) else e.elt
            if _id_like(elt):
                out.add(IDS)
        return out

    def cat(self, e: ast.AST, chain: Tuple, pred: Optional[Callable[[Set], bool]] = None) -> bool:
        return (pred or self.is_cat)(self.labels_of(e, chain))

    # ---- membership polarity
    def pol(self, e: ast.AST, chain: Tuple, sign: int = 1, pred=None) -> Set[int]:
        """+1: the expression is true when the value IS listed; -1: true when it is NOT listed."""
        out: Set[int] = set()
        if isinstance(e, ast.UnaryOp) and isinstance(e.op, ast.Not):
            return self.pol(e.operand, chain, -sign, pred)
        if isinstance(e, ast.BoolOp):
            for v in e.values:
                out |= self.pol(v, chain, sign, pred)
            return out
        if isinstance(e, ast.NamedExpr):
            return self.pol(e.value, chain, sign, pred)
        if isinstance(e, ast.Compare):
            left = e.left
            for op, comp in zip(e.ops, e.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and self.cat(comp, chain, pred):
                    out.add(sign if isinstance(op, ast.In) else -sign)
                if isinstance(comp, ast.Constant) and comp.value is None:
                    inner = self.pol(left, chain, 1, pred)
                    if isinstance(op, (ast.Is, ast.Eq)):
                        out |= {-sign * p for p in inner}
                    elif isinstance(op, (ast.IsNot, ast.NotEq)):
                        out |= {sign * p for p in inner}
                left = comp
            return out
        if isinstance(e, ast.Name):
            return {sign * p for p in self._get(self.flags, e.id, chain, set())}
        if isinstance(e, ast.Call):
            f = e.func
            m, fname = self._call_target(f)
            if m is not None and fname in m.member_funcs:
                return {sign * p for p in m.member_funcs[fname]}
            if isinstance(f, ast.Attribute) and f.attr == "get" and e.args and self.cat(f.value, chain, pred):
                return {sign}
            if isinstance(f, ast.Name) and f.id in ("any", "next") and e.args and isinstance(e.args[0], COMPS):
                g = e.args[0]
                inner = chain + (g,)
                if any(self.has_data(self.labels_of(gen.iter, inner)) or self.cat(gen.iter, inner, pred)
                       for gen in g.generators):
                    for n in ast.walk(g):
                        if isinstance(n, ast.Compare) and any(isinstance(op, (ast.Eq, ast.Is)) for op in n.ops) \
                                and any(_id_like(x) for x in [n.left] + list(n.comparators)):
                            return {sign}
            if isinstance(f, ast.Name) and f.id == "bool" and e.args:
                return self.pol(e.args[0], chain, sign, pred)
        return out

    # ---- sinks
    def is_sink_name(self, name: str, chain: Tuple) -> bool:
        if _tokens(name) & SINK_TOKENS:
            return True
        k = self._scope_of_name(name, chain)
        return name in self.sinks.get(k if k is not None else self._bind_key(chain), set())

    def _add_sink(self, name: str, chain: Tuple) -> bool:
        s = self.sinks.setdefault(self._store_key(name, chain), set())
        if name in s:
            return False
        s.add(name)
        return True

    def has_sink(self, body, chain: Tuple, func_name: Optional[str]) -> Optional[str]:
        items = body if isinstance(body, list) else [body]
        for st in items:
            for n in ast.walk(st):
                if isinstance(n, ast.Raise):
                    return "raises"
                if isinstance(n, ast.Assert):
                    return "asserts"
                if isinstance(n, ast.Call):
                    fn = n.func
                    if isinstance(fn, ast.Attribute) and fn.attr in MUTATORS:
                        r = _root_name(fn.value)
                        if r and self.is_sink_name(r, chain):
                            return f"adds to '{r}'"
                    is_exit = (isinstance(fn, ast.Attribute) and fn.attr == "exit") or \
                        (isinstance(fn, ast.Name) and fn.id in ("exit", "quit"))
                    if is_exit and n.args and not (isinstance(n.args[0], ast.Constant) and n.args[0].value in (0, 2, None)):
                        return "exits non-zero"
                    m, cf = self._call_target(fn)
                    if m is self and cf and cf in self.funcs:
                        for i, a in enumerate(n.args):
                            r = _root_name(a)
                            if r and self.is_sink_name(r, chain):
                                return f"hands '{r}' to {cf}()"
                if isinstance(n, ast.AugAssign):
                    r = _root_name(n.target)
                    if r and self.is_sink_name(r, chain):
                        return f"adds to '{r}'"
                if isinstance(n, ast.Assign):
                    for t in n.targets:
                        if isinstance(t, ast.Subscript):
                            r = _root_name(t)
                            if r and self.is_sink_name(r, chain):
                                return f"writes into '{r}'"
                        if isinstance(t, ast.Name) and FALSEY_FLAGS.match(t.id) and \
                                isinstance(n.value, ast.Constant) and n.value.value is False:
                            return f"sets {t.id} = False"
                if isinstance(n, ast.Return) and n.value is not None:
                    for x in ast.walk(n.value):
                        if isinstance(x, ast.Name) and x.id in EXIT_FAIL_NAMES:
                            return f"returns {x.id}"
                    if func_name == "main" and isinstance(n.value, ast.Constant) and n.value.value in (1, 3):
                        return f"returns {n.value.value} from main"
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in FAIL_WORDS:
                    return f"writes status '{n.value}'"
                if isinstance(n, ast.Dict):
                    for k, v in zip(n.keys, n.values):
                        if isinstance(k, ast.Constant) and k.value in STATUS_KEYS and \
                                isinstance(v, ast.Constant) and isinstance(v.value, str) and \
                                v.value.lower() in ("fail", "failed", "reject", "rejected", "block", "review"):
                            return f"writes {k.value} '{v.value}'"
        return None

    @staticmethod
    def _status_choice(e: ast.IfExp) -> bool:
        for side in (e.body, e.orelse):
            for n in ast.walk(side):
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in FAIL_WORDS:
                    return True
                if isinstance(n, ast.Name) and n.id in EXIT_FAIL_NAMES:
                    return True
        return False

    @staticmethod
    def _container_in_test(test: ast.AST) -> Optional[str]:
        """`if x:`, `if not x:`, `if len(x) …:` -> 'x' (a container whose fullness decides)."""
        t = test.operand if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not) else test
        if isinstance(t, ast.Compare):
            t = t.left
        if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id == "len" and t.args:
            t = t.args[0]
        return t.id if isinstance(t, ast.Name) else None

    # ---- fixpoint
    def _add_labels(self, name: str, chain: Tuple, labels: Set) -> bool:
        if not labels:
            return False
        d = self.labels.setdefault(self._store_key(name, chain), {})
        cur = d.setdefault(name, set())
        if labels <= cur:
            return False
        cur |= labels
        return True

    def _add_flag(self, name: str, chain: Tuple, p: Set[int]) -> bool:
        if not p:
            return False
        d = self.flags.setdefault(self._store_key(name, chain), {})
        cur = d.setdefault(name, set())
        if p <= cur:
            return False
        cur |= p
        return True

    def _fixpoint(self):
        for _ in range(30):
            changed = False
            for node, chain in self.nodes:
                changed |= self._step(node, chain)
            if not changed:
                break

    def _sink_func(self, name: str, pos: Optional[int]) -> bool:
        s = self.sink_funcs.setdefault(name, set())
        if pos in s:
            return False
        s.add(pos)
        return True

    def _step(self, node: ast.AST, chain: Tuple) -> bool:
        ch = False
        fname = self._enclosing_func(chain)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if value is None:
                return False
            pairs = []
            for tg in targets:
                if isinstance(tg, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)) \
                        and len(tg.elts) == len(value.elts):
                    pairs += list(zip(tg.elts, value.elts))
                else:
                    pairs.append((tg, value))
            for tg, val in pairs:
                per_pos = None
                if isinstance(val, ast.Call) and isinstance(tg, (ast.Tuple, ast.List)):
                    cl = self.call_labels(val, chain)
                    if cl is not None and None not in cl:
                        per_pos = cl
                whole = self.labels_of(val, chain) if per_pos is None else set()
                p = self.pol(val, chain, 1)
                for pos, name in _target_names(tg):
                    labs = whole if per_pos is None else per_pos.get(pos, set())
                    ch |= self._add_labels(name, chain, labs)
                    ch |= self._add_flag(name, chain, p)
                if isinstance(val, ast.Call):
                    m, cf = self._call_target(val.func)
                    if m is self and cf:
                        for pos, name in _target_names(tg):
                            if self.is_sink_name(name, chain):
                                ch |= self._sink_func(cf, pos)
            if isinstance(node, ast.AugAssign):
                r = _root_name(node.target)
                if r and self.is_sink_name(r, chain):
                    ch |= self._mark_value_sink(value, chain)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            labs = self.labels_of(node.iter, chain)
            for _, name in _target_names(node.target):
                ch |= self._add_labels(name, chain, labs)
        elif isinstance(node, ast.comprehension):
            labs = self.labels_of(node.iter, chain)
            for _, name in _target_names(node.target):
                d = self.labels.setdefault(id(chain[-1]) if chain else 0, {})
                cur = d.setdefault(name, set())
                if labs and not labs <= cur:
                    cur |= labs
                    ch = True
        elif isinstance(node, ast.Return) and fname and node.value is not None:
            if isinstance(node.value, ast.Tuple):
                pos_l = {i: self.labels_of(el, chain) for i, el in enumerate(node.value.elts)}
            else:
                pos_l = {None: self.labels_of(node.value, chain)}
            r = self.ret.setdefault(fname, {})
            for pos, labs in pos_l.items():
                cur = r.setdefault(pos, set())
                if not labs <= cur:
                    cur |= labs
                    ch = True
            p = self.pol(node.value, chain, 1)
            before = self.member_funcs.get(fname, set())
            if p and not p <= before:
                self.member_funcs[fname] = before | p
                ch = True
            if fname in self.sink_funcs:
                vals = node.value.elts if isinstance(node.value, ast.Tuple) else None
                for pos in list(self.sink_funcs[fname]):
                    target = node.value if (pos is None or vals is None) else (vals[pos] if pos < len(vals) else None)
                    r_name = _root_name(target) if target is not None else None
                    if r_name:
                        ch |= self._add_sink(r_name, chain)
        elif isinstance(node, ast.Call):
            m, cf = self._call_target(node.func)
            if m is self and cf in self.funcs:
                fdef = self.funcs[cf]
                ps = self.params_of(fdef)
                for i in range(len(ps)):
                    a = self._arg_for(node, fdef, i)
                    if a is None:
                        continue
                    if (id(fdef), i) not in self.receives_cat and self.cat(a, chain):
                        self.receives_cat.add((id(fdef), i))
                        ch = True
                    if (id(fdef), i) not in self.receives_data and self.has_data(self.labels_of(a, chain)):
                        self.receives_data.add((id(fdef), i))
                        ch = True
                    r = _root_name(a)
                    if r and self.is_sink_name(r, chain):
                        ch |= self._add_sink(ps[i], (fdef,))
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr in MUTATORS:
                r = _root_name(fn.value)
                if r and self.is_sink_name(r, chain):
                    for a in node.args:
                        ch |= self._mark_value_sink(a, chain)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value.lower() in SINK_KEYS:
                    r = _root_name(v)
                    if r:
                        ch |= self._add_sink(r, chain)
        elif isinstance(node, ast.IfExp):
            if self._status_choice(node):
                for n in ast.walk(node.test):
                    if isinstance(n, ast.Name):
                        ch |= self._add_sink(n.id, chain)
        elif isinstance(node, ast.If):
            if not self.pol(node.test, chain, 1) and self.has_sink(node.body, chain, fname):
                c = self._container_in_test(node.test)
                if c:
                    ch |= self._add_sink(c, chain)
        return ch

    def _mark_value_sink(self, value: ast.AST, chain: Tuple) -> bool:
        ch = False
        r = _root_name(value)
        if r:
            ch |= self._add_sink(r, chain)
        if isinstance(value, ast.Call):
            m, cf = self._call_target(value.func)
            if m is self and cf in self.funcs:
                ch |= self._sink_func(cf, None)
        return ch

    # ---- the audit
    def membership_sites(self, pred=None, only_def=None) -> List[dict]:
        sites: List[dict] = []
        for node, chain in self.nodes:
            if only_def is not None and only_def not in chain:
                continue
            fname = self._enclosing_func(chain)
            if isinstance(node, (ast.If, ast.While, ast.IfExp)):
                p = self.pol(node.test, chain, 1, pred)
                if not p:
                    continue
                body = node.body
                orelse = node.orelse
                fence = None
                if 1 in p:
                    s = self.has_sink(orelse, chain, fname) if orelse else None
                    if s:
                        fence = f"the not-listed branch {s}"
                if -1 in p and not fence:
                    s = self.has_sink(body, chain, fname)
                    if s:
                        fence = f"the not-listed branch {s}"
                sites.append({"line": node.lineno, "code": _seg(self.src, node.test), "fence": fence})
            elif isinstance(node, ast.Assert):
                p = self.pol(node.test, chain, 1, pred)
                if p:
                    sites.append({"line": node.lineno, "code": _seg(self.src, node.test),
                                  "fence": "asserts membership: a not-listed value raises" if 1 in p else None})
            value = None
            if isinstance(node, ast.Assign):
                if any(self.is_sink_name(nm, chain) for t in node.targets for _, nm in _target_names(t)):
                    value = node.value
            elif isinstance(node, ast.AugAssign):
                r = _root_name(node.target)
                if r and self.is_sink_name(r, chain):
                    value = node.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
                r = _root_name(node.func.value)
                if r and self.is_sink_name(r, chain):
                    value = ast.Tuple(elts=list(node.args), ctx=ast.Load())
            elif isinstance(node, ast.Return) and fname and fname in self.sink_funcs:
                value = node.value
            if value is None:
                continue
            for c in ast.walk(value):
                if isinstance(c, COMPS):
                    inner = chain + (c,)
                    for g in c.generators:
                        for cond in g.ifs:
                            if -1 in self.pol(cond, inner, 1, pred):
                                sites.append({"line": getattr(cond, "lineno", node.lineno),
                                              "code": _seg(self.src, cond),
                                              "fence": "a comprehension keeps NOT-listed items and feeds them "
                                                       "into findings"})
        seen, out = set(), []
        for s in sorted(sites, key=lambda x: (x["line"], x["fence"] is None)):
            k = (s["line"], s["code"])
            if k not in seen:
                seen.add(k)
                out.append(s)
        return out

    def fence_funcs(self) -> Dict[str, dict]:
        """{function: first fence} — unconditional fences, and per-parameter fences that bite
        when a caller hands that parameter a catalog (key 'name#i')."""
        if hasattr(self, "_ff"):
            return self._ff
        out: Dict[str, dict] = {}
        lines = {}
        for node, chain in self.nodes:
            if hasattr(node, "lineno"):
                lines.setdefault(node.lineno, chain)
        for s in self.membership_sites():
            if s["fence"]:
                f = self._enclosing_func(lines.get(s["line"], ()))
                if f and f not in out:
                    out[f] = s
        for name, fdef in self.funcs.items():
            for i in range(len(self.params_of(fdef))):
                lab = ("p", id(fdef), i)
                pred = (lambda L, lab=lab: self.is_cat(L) or lab in L)
                for s in self.membership_sites(pred=pred, only_def=fdef):
                    if s["fence"]:
                        out.setdefault(f"{name}#{i}", s)
                        break
        self._ff = out
        return out

    def calls_into(self) -> List[Tuple[int, "ModuleInfo", str, ast.Call, Tuple]]:
        out = []
        for node, chain in self.nodes:
            if isinstance(node, ast.Call):
                m, f = self._call_target(node.func)
                if m is not None and m is not self and f:
                    out.append((node.lineno, m, f, node, chain))
        return out


# ------------------------------------------------------------------ resolver
class Resolver:
    def __init__(self, sources: List[str], roots: List[Path]):
        self.sources, self.roots = sources, roots
        # only package modules and modules sitting beside the audited file are followed
        self.allowed = [PKG_ROOT.resolve()] + [r.resolve() for r in roots[1:2]]
        self.cache: Dict[str, Optional[ModuleInfo]] = {}
        self.depth = 0

    def path_for(self, dotted: str) -> Optional[Path]:
        parts = dotted.split(".")
        cands = []
        for r in self.roots:
            cands.append(r.joinpath(*parts).with_suffix(".py"))
            cands.append(r.joinpath(*parts) / "__init__.py")
        if len(parts) == 1 and SCRIPTS.is_dir():
            cands += [sub / f"{parts[0]}.py" for sub in sorted(SCRIPTS.iterdir()) if sub.is_dir()]
        for c in cands:
            try:
                rc = c.resolve()
                if c.is_file() and any(a == rc.parent or a in rc.parents for a in self.allowed):
                    return c
            except OSError:
                continue
        return None

    def __call__(self, dotted: str) -> Optional[ModuleInfo]:
        p = self.path_for(dotted)
        key = str(p) if p else dotted
        if key in self.cache:
            return self.cache[key]
        self.cache[key] = None  # cycle guard
        if p is None or self.depth >= 2:
            return None
        try:
            src = p.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(p))
        except (OSError, SyntaxError, ValueError, UnicodeDecodeError):
            return None
        self.depth += 1
        try:
            mi = ModuleInfo(p, src, tree, self.sources, self)
        finally:
            self.depth -= 1
        self.cache[key] = mi
        return mi


# ------------------------------------------------------------------ per-gate audit
def _rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(PKG_ROOT))
    except ValueError:
        return str(p)


def audit_file(path: Path, sources: List[str]) -> dict:
    rel = _rel(path)
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, ValueError, UnicodeDecodeError) as e:
        return {"gate": rel, "verdict": "ERROR", "why": f"could not be parsed: {type(e).__name__}: {e}"}
    mi = ModuleInfo(path, src, tree, sources, Resolver(sources, [SCRIPTS, path.parent, path.parent.parent]))
    sites = mi.membership_sites()
    fences = [s for s in sites if s["fence"]]
    inherited = []
    for line, m, f, call, chain in mi.calls_into():
        ff = m.fence_funcs()
        hit = ff.get(f)
        if hit is None and f in m.funcs:
            for i in range(len(m.params_of(m.funcs[f]))):
                if f"{f}#{i}" in ff:
                    a = mi._arg_for(call, m.funcs[f], i)
                    if a is not None and mi.cat(a, chain):
                        hit = ff[f"{f}#{i}"]
                        break
        if hit is not None:
            inherited.append({"line": line, "code": f"{_rel(m.path)}:{f}()",
                              "fence": f"calls a helper that fences on catalog membership "
                                       f"({_rel(m.path)} line {hit['line']}: {hit['code']})"})
    cats = sorted(mi.used_sources)
    res = {"gate": rel, "catalogs_read": cats,
           "membership_tests": [{"line": s["line"], "code": s["code"],
                                 "use": "fence" if s["fence"] else "defaults only"} for s in sites]}
    if fences or inherited:
        allf = fences + inherited
        res["verdict"] = "FAIL"
        res["fences"] = allf
        res["why"] = (f"pass/fail depends on catalog membership: line {allf[0]['line']} `{allf[0]['code']}` — "
                      f"{allf[0]['fence']}")
        return res
    res["verdict"] = "clean"
    if not cats:
        res["why"] = "reads no option catalog: it judges only the measured inputs it is given"
    elif not sites:
        res["why"] = (f"reads {', '.join(cats)} for values only (defaults, budgets, wording); "
                      f"never tests whether an id is listed")
    else:
        res["why"] = (f"reads {', '.join(cats)}; tests membership only to choose defaults (line(s) "
                      f"{', '.join(str(s['line']) for s in sites)}) — the not-listed branch adds no finding "
                      f"and never changes the exit")
    return res


def default_targets() -> List[Path]:
    out: List[Path] = []
    for d in (SCRIPTS / "gates", SCRIPTS / "review"):
        if d.is_dir():
            out += sorted(p for p in d.glob("*.py") if p.name != "__init__.py")
    extra = SCRIPTS / "design" / "divergence.py"
    if extra.is_file():
        out.append(extra)
    return out


def expand(args: List[str]) -> List[Path]:
    out: List[Path] = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            out += sorted(x for x in p.glob("*.py") if x.name != "__init__.py")
        elif p.is_file():
            out.append(p)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 freedom-doctrine audit (static)")
    ap.add_argument("targets", nargs="*", help="gate files or directories (default: every gate)")
    ap.add_argument("--data", default=str(PKG_ROOT / "data"), help="catalog directory")
    ap.add_argument("--all-data", action="store_true", help="treat every data file as a catalog")
    ap.add_argument("--json", action="store_true", help="(output is always JSON)")
    a = ap.parse_args(argv)
    sources, reference = classify_data(Path(a.data), a.all_data)
    targets = expand(a.targets) if a.targets else default_targets()
    if not targets:
        emit({"gate": GATE, "status": "SKIPPED", "summary": "no gate sources found to audit"})
        return EXIT_SKIPPED
    results = [audit_file(p, sources) for p in targets]
    failed = [r for r in results if r["verdict"] == "FAIL"]
    errors = [r for r in results if r["verdict"] == "ERROR"]
    status = "FAIL" if failed else ("ERROR" if errors else "PASS")
    if failed:
        summary = (f"{len(failed)} of {len(results)} gate(s) fence on catalog membership: "
                   + "; ".join(f"{r['gate']} — {r['why']}" for r in failed[:3]))
    elif errors:
        summary = f"{len(errors)} gate(s) could not be parsed; {len(results) - len(errors)} clean"
    else:
        summary = f"all {len(results)} gate(s) judge outcomes only — none fails a value for not being in a catalog"
    emit({"gate": GATE, "status": status, "summary": summary,
          "catalog_sources": sources, "reference_data": reference, "audited": len(results),
          "gates": results,
          "measures": "static data flow from catalog names to membership tests to fail paths; a PASS says no "
                      "gate fences on catalog membership, nothing about whether a gate is good",
          "limits": "taint is followed inside each file and one level into imported package modules; code "
                    "loaded dynamically (importlib from a path) and catalog names built at run time are not "
                    "followed"})
    if failed:
        return EXIT_REVIEW
    return EXIT_ERROR if errors else EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
