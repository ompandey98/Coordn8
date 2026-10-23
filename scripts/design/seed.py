#!/usr/bin/env python3
"""Seeded stimulus draws for the divergence step (H3). Zero third-party deps.

Ideas drift toward a model's habits when the model picks its own "random" word. So the
technique, the constraint, the distant field and the random word are drawn HERE, from
`random.Random(seed)`, and every draw is appended to `Draw.log`. The same seed replays the
same draws in any process, on any machine, on any Python 3 version:

  * the seed is derived with SHA-256 (never Python's salted `hash()`), and
  * only `Random.random()` is used for picking. The standard library guarantees that
    `random()` keeps producing the same sequence for the same integer seed across Python
    versions; `choice()`/`sample()` carry no such guarantee, so they are not used.

    from design.seed import Draw, derive_seed          # with scripts/ on sys.path
    d = Draw(derive_seed("payments-api", 4))
    t = d.technique("design")                          # a technique matched to the problem type
    d.constraint(); d.field(); d.word(); d.sample("words", 3)
    d.record()   ->  {"seed": ..., "catalog_hash": ..., "draws": [...]}   # store in divergence.json
    replay(seed, draws)  ->  []                         # [] = the recorded draws are real

The catalog (data/idea_techniques.json) is a menu, not a fence. A problem type the catalog
does not list still gets a draw (from every technique, recorded as matched=false), and an
option may use a technique that was never drawn at all. Nothing here judges an option.

CLI:
    seed.py draw --problem design --n 3 [--seed N | --project SLUG [--build K]] [--json]
    seed.py derive --project SLUG --build K
    seed.py replay RECORD.json            # {"seed": N, "draws": [...]}  (or a divergence decision)
Exit codes (scripts/lib/common.py): 0 PASS, 1 REVIEW (replay mismatch), 3 ERROR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import secrets
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW  # noqa: E402
from lib.paths import PKG_ROOT  # noqa: E402

CATALOG_ENV = "COORDN8_IDEA_CATALOG"
POOL_KINDS = {"constraint": "constraints", "field": "fields", "word": "words", "oblique": "oblique"}


# ------------------------------------------------------------------ catalog
def catalog_path() -> Path:
    env = os.environ.get(CATALOG_ENV)
    return Path(env).expanduser() if env else PKG_ROOT / "data" / "idea_techniques.json"


def load_catalog(path=None) -> dict:
    p = Path(path) if path else catalog_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("techniques"), list):
        raise ValueError(f"{p}: not an idea-technique catalog")
    return data


def catalog_hash(catalog: dict) -> str:
    """Hash of what the draws depend on (technique ids/good_for/stimulus + pools)."""
    core = {"techniques": [[t.get("id"), sorted(t.get("good_for") or []), t.get("stimulus")]
                           for t in catalog.get("techniques", [])],
            "pools": catalog.get("pools", {})}
    blob = json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ------------------------------------------------------------------ seeds
def _norm_slug(slug) -> str:
    return str(slug).strip().lower()


def derive_seed(project_slug, build_count) -> int:
    """Stable 48-bit seed from (project slug, build count). SHA-256, never hash()."""
    blob = f"coordn8-seed|{_norm_slug(project_slug)}|{int(build_count)}".encode("utf-8")
    return int(hashlib.sha256(blob).hexdigest()[:12], 16)


def decision_seed(build_seed: int, decision: str) -> int:
    """A separate stable seed per pivotal decision of one build, so two decisions of the
    same problem type do not receive the identical draw sequence."""
    blob = f"coordn8-decision|{int(build_seed)}|{str(decision).strip().lower()}".encode("utf-8")
    return int(hashlib.sha256(blob).hexdigest()[:12], 16)


def fresh_seed() -> int:
    return secrets.randbits(48)


# ------------------------------------------------------------------ draws
class Draw:
    """A recorded stream of seeded draws. Every public draw appends one entry to .log:
    {"i", "kind", "args", "value"} — enough for replay() to re-derive it."""

    def __init__(self, seed, catalog: dict | None = None):
        self.seed = int(seed)
        self._rng = random.Random(self.seed)
        self.catalog = catalog if catalog is not None else load_catalog()
        self.catalog_hash = catalog_hash(self.catalog)
        self.log: list = []

    # -- primitives (random() only: version-stable)
    def _index(self, n: int) -> int:
        if n <= 0:
            raise ValueError("cannot draw from an empty pool")
        i = int(self._rng.random() * n)
        return min(i, n - 1)

    def _sample(self, seq: list, k: int) -> list:
        items = list(seq)
        k = int(k)
        if k < 0 or k > len(items):
            raise ValueError(f"cannot sample {k} from a pool of {len(items)}")
        for j in range(k):  # partial Fisher-Yates
            r = j + self._index(len(items) - j)
            items[j], items[r] = items[r], items[j]
        return items[:k]

    def _record(self, kind: str, args: dict, value, **extra):
        entry = {"i": len(self.log), "kind": kind, "args": args, "value": value}
        entry.update(extra)
        self.log.append(entry)
        return value

    def _pool(self, name: str) -> list:
        pools = self.catalog.get("pools") or {}
        if name not in pools or not isinstance(pools[name], list):
            raise KeyError(f"unknown stimulus pool: {name}")
        return pools[name]

    # -- public draws
    def technique(self, problem_type: str, exclude=()) -> dict:
        """Draw one technique whose good_for lists problem_type (excluding ids already used).
        An unlisted problem type draws from every technique (matched=False) — never an error."""
        excl = sorted({str(x) for x in (exclude or ())})
        techs = sorted((t for t in self.catalog.get("techniques", []) if t.get("id") not in excl),
                       key=lambda t: t.get("id", ""))
        matched = [t for t in techs if problem_type in (t.get("good_for") or [])]
        pool, is_matched = (matched, True) if matched else (techs, False)
        if not pool:
            raise ValueError("no technique left to draw (every one excluded)")
        t = pool[self._index(len(pool))]
        self._record("technique", {"problem_type": problem_type, "exclude": excl}, t["id"],
                     matched=is_matched, pool_size=len(pool))
        return t

    def _pool_draw(self, kind: str) -> str:
        pool = self._pool(POOL_KINDS[kind])
        v = pool[self._index(len(pool))]
        return self._record(kind, {}, v, pool_size=len(pool))

    def constraint(self) -> str:
        return self._pool_draw("constraint")

    def field(self) -> str:
        return self._pool_draw("field")

    def word(self) -> str:
        return self._pool_draw("word")

    def oblique(self) -> str:
        return self._pool_draw("oblique")

    def sample(self, pool, k: int) -> list:
        """k distinct items from a named catalog pool ("words", "fields", …) or a literal list."""
        if isinstance(pool, str):
            items, arg = self._pool(pool), pool
        else:
            items = list(pool)
            arg = items
        v = self._sample(items, k)
        return self._record("sample", {"pool": arg, "k": int(k)}, v, pool_size=len(items))

    def stimulus_for(self, technique: dict):
        """Draw the stimulus a technique asks for (constraint/field/word/oblique) or None."""
        kind = technique.get("stimulus")
        if kind in POOL_KINDS:
            return kind, self._pool_draw(kind)
        return None, None

    def draw_set(self, problem_type: str, n: int = 3) -> list:
        """n DIFFERENT techniques for one decision, each with its own drawn stimulus."""
        used: list = []
        out = []
        for _ in range(int(n)):
            t = self.technique(problem_type, exclude=used)
            used.append(t["id"])
            skind, sval = self.stimulus_for(t)
            out.append({"technique": t["id"], "name": t.get("name"), "stimulus_kind": skind,
                        "stimulus": sval, "how_to": t.get("how_to")})
        return out

    def record(self) -> dict:
        return {"seed": self.seed, "catalog_hash": self.catalog_hash, "draws": [dict(e) for e in self.log]}


def replay(seed, draws, catalog: dict | None = None) -> list:
    """Re-run the recorded draws from the seed. Returns a list of mismatch strings ([] = real)."""
    if not isinstance(draws, list) or not draws:
        return ["no draws recorded"]
    d = Draw(seed, catalog)
    problems = []
    for n, e in enumerate(draws):
        if not isinstance(e, dict) or "kind" not in e or "value" not in e:
            problems.append(f"draw {n}: not a seed.py record (needs kind/args/value)")
            continue
        kind, args = e["kind"], e.get("args") or {}
        try:
            if kind == "technique":
                got = d.technique(args.get("problem_type", ""), args.get("exclude") or ())["id"]
            elif kind in POOL_KINDS:
                got = d._pool_draw(kind)
            elif kind == "sample":
                got = d.sample(args.get("pool"), args.get("k", 0))
            else:
                problems.append(f"draw {n}: unknown kind {kind!r}")
                continue
        except (KeyError, ValueError, TypeError) as ex:
            problems.append(f"draw {n}: cannot replay ({ex})")
            continue
        if got != e["value"]:
            problems.append(f"draw {n} ({kind}): recorded {e['value']!r} but seed {seed} gives {got!r}")
    return problems


# ------------------------------------------------------------------ CLI
def _builds_recorded(project: str) -> int:
    """How many builds of this project the user-level choice history already holds."""
    try:
        from memory.store import Store
        rows = Store(create=False).recent_choices(10 ** 9)
    except Exception:  # a missing/odd store must never break a draw
        return 0
    slug = _norm_slug(project)
    return sum(1 for r in rows if _norm_slug(r.get("project") or "") == slug)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="seed.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draw")
    p.add_argument("--problem", required=True)
    p.add_argument("--n", type=int, default=3)
    p.add_argument("--seed", type=int)
    p.add_argument("--project")
    p.add_argument("--build", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("derive")
    p.add_argument("--project", required=True)
    p.add_argument("--build", type=int, required=True)
    p = sub.add_parser("replay")
    p.add_argument("record")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "derive":
            print(json.dumps({"project": a.project, "build": a.build,
                              "seed": derive_seed(a.project, a.build)}))
            return EXIT_PASS
        if a.cmd == "replay":
            rec = json.loads(Path(a.record).read_text(encoding="utf-8"))
            bad = replay(rec.get("seed"), rec.get("draws"))
            print(json.dumps({"seed": rec.get("seed"), "replayed": not bad, "mismatches": bad}))
            return EXIT_REVIEW if bad else EXIT_PASS
        # draw
        if a.seed is not None:
            seed, source, build = a.seed, "given", a.build
        elif a.project:
            build = a.build if a.build is not None else _builds_recorded(a.project) + 1
            seed, source = derive_seed(a.project, build), "derived"
        else:
            seed, source, build = fresh_seed(), "fresh", None
        d = Draw(seed)
        options = d.draw_set(a.problem, a.n)
        out = {"seed": seed, "seed_source": source, "project": a.project, "build": build,
               "problem_type": a.problem, "options": options, **d.record()}
        if a.json:
            print(json.dumps(out, ensure_ascii=False, indent=1))
        else:
            print(f"seed {seed} ({source}) · problem: {a.problem}")
            for i, o in enumerate(options, 1):
                stim = f" · {o['stimulus_kind']}: {o['stimulus']}" if o["stimulus"] else ""
                print(f"  {i}. {o['name']} [{o['technique']}]{stim}")
        return EXIT_PASS
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as ex:
        sys.stderr.write(f"[coordn8:seed] ERROR: {ex}\n")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
