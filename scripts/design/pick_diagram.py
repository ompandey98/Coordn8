#!/usr/bin/env python3
"""Coordn8 — diagram type selector (B1) with separate detail and audience dials (C5).

Two-axis choice, made mechanically so it can be tested and explained:

  step 1  BEHAVIOUR  what the content does (fan-in, staged process, decision branching, lifecycle,
                     messages over time, quantity split, ...). Every behaviour in
                     data/diagram_types.json is scored: distinct text signals matched (capped) plus the
                     weights of the structural features the description actually has (a node with 3+
                     incoming relations, conditions on outgoing relations, numeric amounts on relations,
                     zones, a time axis ...). The DOMINANT behaviour wins; the runner-up is recorded.
  step 2  LAYOUT     the dominant behaviour's suggested types (plus the runner-up's, at lower weight)
                     are scored: suggestion rank + fit weights - a penalty per unmet requirement.

Tie-breakers: a 3-column table that says the same thing wins (and the plan says so); pick the
dominant axis. Every plan entry lists >= 2 rejected options with reasons.

Freedom (H1): the catalog is a menu, never a fence. A caller may pass `invented: {name, why}` and
the invented form is always accepted — selection still runs so the plan records what was
considered and why it was passed over.

Dials (C5) are SEPARATE:
  detail   low | medium | high — how MANY nodes are drawn (low keeps half, most important first;
           medium keeps up to the type's node budget; high keeps all). It never renames a node.
  audience plain | mixed | field — what nodes are CALLED. It never drops a node.

Input (desc.json):
  {"id": "fig-x", "text": "...", "items": [...], "relations": [...], "has_time_axis": false,
   "attributes": [...], "audience": "plain", "detail": "medium", "invented": {"name": "...", "why": "..."},
   "accents": 0, "callouts": 0}
  items:     strings, or {id, label?, plain?, field?, kind?, attrs?{}, value?, time?, zone?, layer?,
             trace?, importance?, focal?}
  relations: {from, to, kind?, label?, when?, value?, time?}

CLI:
  python3 scripts/design/pick_diagram.py <desc.json> [--build DIR] [--figure ID] [--json]
         [--detail low|medium|high] [--audience plain|mixed|field] [--catalog PATH]
  --build DIR writes/merges the figure entry into DIR/diagram_plan.json (SPEC 3.4).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.common import EXIT_ERROR, EXIT_PASS  # noqa: E402

PKG_ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = PKG_ROOT / "data" / "diagram_types.json"

MESSAGE_KINDS = {"message", "call", "request", "response", "reply", "return", "event", "send", "notify"}
ACTOR_KINDS = {"actor", "participant", "client", "service", "user", "system"}
STATE_KINDS = {"state", "phase", "status"}
TRANSITION_KINDS = {"transition", "event-transition"}
CONTAIN_KINDS = {"contains", "part_of", "part-of", "child", "parent", "has_part", "includes"}
DEPEND_KINDS = {"depends", "depends_on", "depends-on", "requires", "imports", "uses", "needs"}
OWNER_KINDS = {"team", "person", "role", "owner", "agent", "group", "department"}
DECISION_KINDS = {"decision", "question", "branch", "gate"}
DETAIL_LEVELS = ("low", "medium", "high")
AUDIENCE_LEVELS = ("plain", "mixed", "field")


# ---------------------------------------------------------------- catalog ----
_CATALOG_CACHE: dict = {}


def load_catalog(path=None) -> dict:
    p = Path(path) if path else CATALOG_PATH
    key = str(p)
    if key not in _CATALOG_CACHE:
        _CATALOG_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
    return _CATALOG_CACHE[key]


def type_index(cat: dict) -> dict:
    return {t["id"]: t for t in cat.get("types", [])}


# --------------------------------------------------------- normalisation ----
def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")
    return s or "item"


def normalise(desc: dict):
    """Return (items, relations) with stable ids; relations resolved to item ids."""
    items = []
    seen = set()
    for i, it in enumerate(desc.get("items") or []):
        if not isinstance(it, dict):
            it = {"label": str(it)}
        it = dict(it)
        iid = str(it.get("id") or _slug(it.get("label") or f"item-{i + 1}"))
        base, k = iid, 2
        while iid in seen:
            iid = f"{base}-{k}"
            k += 1
        seen.add(iid)
        it["id"] = iid
        it.setdefault("label", iid)
        it["_index"] = i
        items.append(it)
    by_key = {}
    for it in items:
        by_key[it["id"]] = it["id"]
        by_key.setdefault(str(it["label"]), it["id"])
        by_key.setdefault(str(it["label"]).lower(), it["id"])
    rels = []
    for r in desc.get("relations") or []:
        if not isinstance(r, dict):
            continue
        a = r.get("from", r.get("source"))
        b = r.get("to", r.get("target"))
        if a is None or b is None:
            continue
        for end in (a, b):
            if str(end) not in by_key and str(end).lower() not in by_key:
                nid = _slug(end)
                items.append({"id": nid, "label": str(end), "_index": len(items), "_implicit": True})
                by_key[str(end)] = nid
                by_key[nid] = nid
        rr = dict(r)
        rr["from"] = by_key.get(str(a), by_key.get(str(a).lower()))
        rr["to"] = by_key.get(str(b), by_key.get(str(b).lower()))
        rels.append(rr)
    return items, rels


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _attrs(it: dict) -> dict:
    a = it.get("attrs")
    return a if isinstance(a, dict) else {}


# -------------------------------------------------------------- features ----
def _has_cycle(ids, out_adj) -> bool:
    color = {n: 0 for n in ids}
    for start in ids:
        if color[start]:
            continue
        stack = [(start, iter(out_adj.get(start, ())))]
        color[start] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = 2
                stack.pop()
                continue
            if color.get(nxt, 0) == 1:
                return True
            if color.get(nxt, 0) == 0:
                color[nxt] = 1
                stack.append((nxt, iter(out_adj.get(nxt, ()))))
    return False


def compute_features(desc: dict, items: list, rels: list) -> dict:
    ids = [it["id"] for it in items]
    byid = {it["id"]: it for it in items}
    indeg = {n: 0 for n in ids}
    outdeg = {n: 0 for n in ids}
    out_adj: dict = {n: [] for n in ids}
    cond_out = {n: 0 for n in ids}
    for r in rels:
        outdeg[r["from"]] += 1
        indeg[r["to"]] += 1
        out_adj[r["from"]].append(r["to"])
        if r.get("when") or r.get("condition"):  # a condition, not a mere label (messages carry labels)
            cond_out[r["from"]] += 1
    kinds = lambda S: [it for it in items if str(it.get("kind", "")).lower() in S]  # noqa: E731
    rkinds = lambda S: [r for r in rels if str(r.get("kind", "")).lower() in S]  # noqa: E731
    cyc = _has_cycle(ids, out_adj) if rels else False
    roots = [n for n in ids if indeg[n] == 0]
    tree = False
    if len(ids) >= 3 and rels and len(rels) == len(ids) - 1 and len(roots) == 1 \
            and all(indeg[n] <= 1 for n in ids) and not cyc and max(outdeg.values()) >= 2:
        seen, todo = set(), [roots[0]]
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            todo.extend(out_adj[n])
        tree = len(seen) == len(ids)
    chain = False
    if len(rels) >= 2 and not cyc and all(indeg[n] <= 1 and outdeg[n] <= 1 for n in ids):
        touched = {r["from"] for r in rels} | {r["to"] for r in rels}
        chain = len([n for n in touched if indeg[n] == 0]) == 1 and len(rels) == len(touched) - 1
    vals = [it.get("value") for it in items if _num(it.get("value"))]
    zones = {str(it["zone"]) for it in items if it.get("zone") not in (None, "")}
    crossing = any(byid[r["from"]].get("zone") and byid[r["to"]].get("zone")
                   and byid[r["from"]].get("zone") != byid[r["to"]].get("zone") for r in rels)
    explicit_items = [it for it in items if not it.get("_implicit")]
    attr_counts = [len(_attrs(it)) for it in explicit_items]
    if explicit_items and not any(attr_counts) and desc.get("attributes"):
        attr_counts = [len(desc["attributes"])] * len(explicit_items)
    avg_attrs = (sum(attr_counts) / len(attr_counts)) if attr_counts else 0.0
    numeric_counts = [sum(1 for v in _attrs(it).values() if _num(v)) for it in explicit_items]
    two_axes = len(explicit_items) >= 3 and all(
        2 <= n <= 3 and n == len(_attrs(it)) for n, it in zip(numeric_counts, explicit_items))
    traces = desc.get("traces")
    trace_ids = {str(it["trace"]) for it in items if it.get("trace") not in (None, "")}
    timed = [it for it in items if it.get("time") not in (None, "") or it.get("date") not in (None, "")]
    f = {
        "no_relations": bool(items) and len(rels) == 0,
        "has_relations": len(rels) > 0,
        "fan_in": bool(ids) and max(indeg.values()) >= 3,
        "branching": any(outdeg[n] >= 2 and cond_out[n] >= 2 for n in ids) or bool(kinds(DECISION_KINDS)),
        "linear_chain": chain,
        "tree_shaped": tree,
        "multi_parent": bool(ids) and max(indeg.values()) >= 2,
        "has_cycle": cyc,
        "edge_quantities": any(_num(r.get("value")) for r in rels),
        "item_quantities": len(vals) >= 2,
        "signed_values": any(v < 0 for v in vals) and any(v > 0 for v in vals),
        "time_axis": bool(desc.get("has_time_axis")) or len(timed) >= 2
        or sum(1 for r in rels if r.get("time") not in (None, "")) >= 2,
        "messages": len(rkinds(MESSAGE_KINDS)) >= 2,
        "actors": len(kinds(ACTOR_KINDS)) >= 2,
        "zones": len(zones) >= 2,
        "zone_crossing": bool(crossing),
        "layered_items": sum(1 for it in items if it.get("layer") not in (None, "")
                             or it.get("level") not in (None, "")) >= 3,
        "attributes3": avg_attrs >= 3,
        "two_numeric_axes": two_axes,
        "states": len(kinds(STATE_KINDS)) >= 3 or len(rkinds(TRANSITION_KINDS)) >= 2,
        "containment": len(rkinds(CONTAIN_KINDS)) >= 2,
        "depends": len(rkinds(DEPEND_KINDS)) >= 2,
        "two_traces": (isinstance(traces, list) and len(traces) == 2) or len(trace_ids) == 2,
        "owners": len(kinds(OWNER_KINDS)) >= 2,
        "many_items": len(ids) > 9,
    }
    stats = {"items": len(ids), "relations": len(rels), "max_in": max(indeg.values()) if ids else 0,
             "max_out": max(outdeg.values()) if ids else 0, "avg_attributes": round(avg_attrs, 2),
             "indeg": indeg, "outdeg": outdeg}
    return {"features": f, "stats": stats}


# ------------------------------------------------------------- scoring ----
def _signal_hits(text: str, signals: list) -> list:
    hits = []
    for s in signals:
        pat = r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])"
        if re.search(pat, text):
            hits.append(s)
    return hits


def score_behaviours(cat: dict, text: str, feats: dict) -> list:
    cap = int(cat.get("selection", {}).get("text_signal_cap", 5))
    out = []
    for order, b in enumerate(cat.get("behaviours", [])):
        hits = _signal_hits(text, b.get("signals", []))
        struct = [(k, w) for k, w in b.get("structure", {}).items() if feats.get(k)]
        score = min(cap, len(hits)) + sum(w for _, w in struct)
        out.append({"id": b["id"], "label": b.get("label", b["id"]), "score": round(score, 3),
                    "signals": hits, "structure": [k for k, _ in struct], "_order": order})
    out.sort(key=lambda x: (-x["score"], x["_order"]))
    return out


def score_types(cat: dict, dominant: dict, runner: dict, feats: dict, structure_known: bool = True,
                text: str = "") -> list:
    tix = type_index(cat)
    bix = {b["id"]: b for b in cat.get("behaviours", [])}
    sel = cat.get("selection", {})
    penalty = float(sel.get("unmet_requirement_penalty", 4))
    tsw = float(sel.get("type_signal_weight", 0.75))
    tcap = int(sel.get("type_signal_cap", 3))
    base: dict = {}
    origin: dict = {}
    for rank, t in enumerate(bix[dominant["id"]]["suggest"]):
        base[t] = 3.0 - 0.5 * rank
        origin[t] = "dominant"
    if runner and runner["score"] > 0:
        for rank, t in enumerate(bix[runner["id"]]["suggest"]):
            b = max(0.25, 1.5 - 0.5 * rank)
            if t in base:
                base[t] += 0.5
            else:
                base[t] = b
                origin[t] = "runner-up"
    out = []
    for order, (tid, b) in enumerate(base.items()):
        t = tix.get(tid)
        if not t:
            continue
        fit = [(k, w) for k, w in t.get("fit", {}).items() if feats.get(k)]
        missing_fit = [k for k in t.get("fit", {}) if not feats.get(k)]
        # unknown structure is not absent structure: with no items given, requirements are not penalised
        unmet = [k for k in t.get("requires", []) if not feats.get(k)] if structure_known else []
        thits = _signal_hits(text, t.get("signals", [])) if text else []
        s = b + sum(w for _, w in fit) + tsw * min(tcap, len(thits)) - penalty * len(unmet)
        out.append({"type": tid, "score": round(s, 3), "fit": [k for k, _ in fit], "missing_fit": missing_fit,
                    "unmet": unmet, "signals": thits, "origin": origin[tid], "_order": order})
    out.sort(key=lambda x: (-x["score"], x["_order"]))
    return out


def fits_three_column_table(feats: dict, items: list, stats: dict) -> bool:
    explicit = [it for it in items if not it.get("_implicit")]
    return (feats["no_relations"] and not feats["branching"] and 0 < len(explicit) <= 12
            and stats["avg_attributes"] <= 2)


# ---------------------------------------------------------------- dials ----
def humanise(s: str) -> str:
    s = str(s)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"[_\-./:]+", " ", s).strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return s
    if s.isupper() and len(s) <= 5:  # keep short acronyms (API, DMZ)
        return s
    words = s.split(" ")
    words = [w if (w.isupper() and len(w) <= 5) else w.lower() for w in words]
    out = " ".join(words)
    return out[0].upper() + out[1:]


def node_name(it: dict, audience: str) -> dict:
    """Audience dial only: the name used for a node. Never decides whether it is drawn."""
    field = str(it.get("field") or it.get("label") or it["id"])
    plain = str(it.get("plain") or humanise(it.get("label") or it["id"]))
    if audience == "field":
        return {"name": field}
    if audience == "mixed":
        return {"name": plain, "sub": field} if field.lower() != plain.lower() else {"name": plain}
    return {"name": plain}


def keep_nodes(items: list, stats: dict, detail: str, budget_nodes: int) -> list:
    """Detail dial only: which node ids are drawn. Never decides what they are called."""
    deg = {it["id"]: stats["indeg"].get(it["id"], 0) + stats["outdeg"].get(it["id"], 0) for it in items}
    ranked = sorted(items, key=lambda it: (-(it.get("importance") if _num(it.get("importance")) else 0),
                                           -deg[it["id"]], it["_index"]))
    n = len(items)
    if detail == "low":
        k = max(1, int(math.ceil(n / 2.0))) if n else 0
    elif detail == "medium":
        k = min(n, max(1, int(budget_nodes)))
    else:
        k = n
    keep = {it["id"] for it in ranked[:k]}
    return [it["id"] for it in items if it["id"] in keep]  # input order preserved


# ------------------------------------------------------------- reasons ----
def _feature_text(cat: dict, k: str) -> str:
    return cat.get("features", {}).get(k, k)


def _reject_reason(cat: dict, cand: dict, chosen_id: str, chosen_score: float, behaviour_label: str) -> str:
    tix = type_index(cat)
    if cand["unmet"]:
        need = "; ".join(_feature_text(cat, k) for k in cand["unmet"])
        return f"needs what this content does not have: {need}"
    chosen = tix.get(chosen_id, {})
    for nt in chosen.get("not_this", []):
        if nt["type"] == cand["type"]:
            return (f"the better choice only when {nt['when']}; that is not what this content shows "
                    f"(scored {cand['score']} vs {chosen_score} for {behaviour_label})")
    gap = ", ".join(cand["missing_fit"][:2])
    tail = f"; lacks {gap}" if gap else ""
    if cand["score"] >= chosen_score:
        return (f"tied with {chosen_id} on score ({cand['score']}) for {behaviour_label}; ranked behind it by "
                f"the behaviour's suggestion order{tail} — a close call, either reads")
    return f"scored lower for {behaviour_label} ({cand['score']} vs {chosen_score}){tail}"


def _table_reason(feats: dict, shape: bool) -> str:
    if feats["branching"]:
        return "branches carry the meaning; a table would flatten them"
    if not feats["no_relations"]:
        return "relations between the items carry the meaning; a table would drop them"
    if shape:
        return "the shape of the values is the message; a table hides the pattern"
    if feats["layered_items"]:
        return "the stacking order of the layers carries the meaning; a table reads as a flat list"
    if feats["zones"]:
        return "which zone each item sits in carries the meaning; a table loses the boundaries"
    if feats["time_axis"]:
        return "spacing in time carries the meaning; a table reads every row as equally spaced"
    return "a 3-column table would not say the same: the layout carries structure the rows cannot"


# ---------------------------------------------------------------- plan ----
def plan_figure(desc: dict, catalog=None, figure_id=None, detail=None, audience=None) -> dict:
    cat = catalog if isinstance(catalog, dict) else load_catalog(catalog)
    tix = type_index(cat)
    bix = {b["id"]: b for b in cat.get("behaviours", [])}
    gb = dict(cat.get("global_budget", {"nodes": 9, "edges": 12, "accents": 2, "callouts": 2}))
    items, rels = normalise(desc)
    fs = compute_features(desc, items, rels)
    feats, stats = fs["features"], fs["stats"]
    text = " ".join(str(x) for x in [desc.get("text", ""), desc.get("title", "")]).lower()

    ranked = score_behaviours(cat, text, feats)
    dominant = ranked[0] if ranked else None
    fallback = None
    if not dominant or dominant["score"] <= 0:
        fid = "topology" if rels else "attribute-comparison"
        dominant = next(x for x in ranked if x["id"] == fid)
        fallback = "no signal or structure matched; fell back by structure (relations present)" if rels \
            else "no signal or structure matched; fell back by structure (no relations)"
    runner = next((x for x in ranked if x["id"] != dominant["id"]), None)
    shape = bool(bix[dominant["id"]].get("shape_is_message"))

    structure_known = bool(items)
    cands = score_types(cat, dominant, runner, feats, structure_known, text)
    chosen = cands[0]["type"] if cands else "table"
    chosen_score = cands[0]["score"] if cands else 0.0

    tie = None
    if feats["attributes3"] and feats["no_relations"] and not feats["branching"] and not shape:
        tie = {"rule": "table-attributes",
               "why": f"items carry {stats['avg_attributes']} attributes each with no relations or branches: "
                      "a table says the same thing, so the table wins"}
    elif tix.get(chosen, {}).get("table_equivalent") and chosen != "table" \
            and fits_three_column_table(feats, items, stats):
        tie = {"rule": "table-equivalent",
               "why": f"{chosen} is table-equivalent here: a 3-column table holds the same content, so the "
                      "table wins"}
    elif feats["attributes3"] and feats["no_relations"] and shape:
        tie = {"rule": "table-considered",
               "why": "items carry 3+ attributes but the shape of the values is the message, so the table "
                      "tie-breaker does not apply", "applied": False}
    if tie and tie.get("applied", True) and chosen != "table":
        pre = chosen
        chosen = "table"
        chosen_score = max(chosen_score, next((c["score"] for c in cands if c["type"] == "table"), chosen_score))
        tie["replaced"] = pre
    if tie and "applied" not in tie:
        tie["applied"] = True

    invented = None
    inv = desc.get("invented")
    if isinstance(inv, dict) and str(inv.get("name") or "").strip():
        invented = {"name": str(inv["name"]).strip(), "why": str(inv.get("why") or "").strip()}
    elif isinstance(inv, str) and inv.strip():
        invented = {"name": inv.strip(), "why": str(desc.get("invented_why") or "").strip()}

    # rejected options (>= 2, with reasons)
    rejected = []
    pool = [c for c in cands if c["type"] != chosen] if not invented else list(cands)
    for c in pool:
        if c["type"] == "table":
            continue
        if tie and tie.get("applied") and chosen == "table":
            why = (f"a 3-column table says the same thing (tie-breaker), so the table wins over this option "
                   f"(its own score {c['score']})")
            if c["unmet"]:
                why += "; it also " + _reject_reason(cat, c, chosen, chosen_score, dominant["label"])
        else:
            why = _reject_reason(cat, c, chosen, chosen_score, dominant["label"])
        if invented:
            why = f"caller invented '{invented['name']}' instead: {invented['why'] or '(no reason given)'}; " \
                  f"catalog fit {c['score']}"
        rejected.append({"type": c["type"], "why": why})
        if len(rejected) >= 3:
            break
    primary = bix[dominant["id"]]["suggest"][0] if bix[dominant["id"]].get("suggest") else None
    if primary and primary not in (chosen, "table") and all(r["type"] != primary for r in rejected):
        pc = next((c for c in cands if c["type"] == primary), None)
        if pc:
            why = _reject_reason(cat, pc, chosen, chosen_score, dominant["label"])
            rejected.append({"type": primary, "why": f"first suggestion for {dominant['label']}, but {why}"})
    if chosen != "table" and not any(r["type"] == "table" for r in rejected):
        rejected.append({"type": "table", "why": _table_reason(feats, shape)})
    if len(rejected) < 2:
        for nt in tix.get(chosen, {}).get("not_this", []):
            if nt["type"] != chosen and all(r["type"] != nt["type"] for r in rejected):
                rejected.append({"type": nt["type"], "why": f"the better choice only when {nt['when']}"})
            if len(rejected) >= 2:
                break

    # budget, dials
    t = tix.get(chosen) if not invented else None
    budget = dict(gb)
    if t:
        budget.update(t.get("budget", {}))
    if invented and isinstance(inv, dict) and isinstance(inv.get("budget"), dict):
        budget.update({k: v for k, v in inv["budget"].items() if _num(v)})
    detail = (detail or desc.get("detail") or "medium").lower()
    if detail not in DETAIL_LEVELS:
        detail = "medium"
    audience = (audience or desc.get("audience") or "plain").lower()
    if audience not in AUDIENCE_LEVELS:
        audience = "plain"
    kept = keep_nodes(items, stats, detail, int(budget.get("nodes", gb["nodes"])))
    keptset = set(kept)
    byid = {it["id"]: it for it in items}
    edges_kept = [[r["from"], r["to"]] for r in rels if r["from"] in keptset and r["to"] in keptset]
    names, subs = {}, {}
    for nid in kept:
        nm = node_name(byid[nid], audience)
        names[nid] = nm["name"]
        if "sub" in nm:
            subs[nid] = nm["sub"]
    cuts = []
    dropped = [it for it in items if it["id"] not in keptset]
    if dropped:
        cuts.append(f"detail={detail}: dropped {len(dropped)} of {len(items)} nodes "
                    f"(least important/connected): " + ", ".join(str(it.get("label", it["id"])) for it in dropped))
        lost = len(rels) - len(edges_kept)
        if lost:
            cuts.append(f"detail={detail}: {lost} relation(s) touching dropped nodes moved to a detail figure")
    accents = desc.get("accents")
    if not _num(accents):
        accents = sum(1 for nid in kept if byid[nid].get("focal"))
    callouts = desc.get("callouts")
    callouts = len(callouts) if isinstance(callouts, list) else (callouts if _num(callouts) else 0)

    variant = None
    if t:
        for v in t.get("variants", []):
            if feats.get(v.get("when_feature")):
                variant = v["id"]
                break
    layout = (invented and isinstance(inv, dict) and inv.get("layout")) or (t or {}).get("layout") or \
        ("grid" if chosen == "table" else "freeform")
    fig_id = str(figure_id or desc.get("id") or "fig-1")
    entry = {
        "id": fig_id,
        "behaviour": dominant["label"],
        "behaviour_id": dominant["id"],
        "runner_up": ({"id": runner["id"], "label": runner["label"], "score": runner["score"]}
                      if runner and runner["score"] > 0 else None),
        "behaviour_scores": {x["id"]: x["score"] for x in ranked[:5]},
        "evidence": {"signals": dominant["signals"], "structure": dominant["structure"],
                     "features": sorted(k for k, v in feats.items() if v)},
        "type": invented["name"] if invented else chosen,
        "catalog_choice": chosen,
        "type_scores": {c["type"]: c["score"] for c in cands[:6]},
        "layout": layout,
        "variant": variant,
        "invented": invented,
        "tie_breaker": tie,
        "rejected": rejected,
        "nodes": len(kept),
        "edges": len(edges_kept),
        "accents": int(accents),
        "callouts": int(callouts),
        "budget": budget,
        "detail": detail,
        "audience_names": audience,
        "kept": kept,
        "names": names,
        "edges_kept": edges_kept,
        "cuts": cuts,
        "source_nodes": len(items),
        "source_edges": len(rels),
    }
    if subs:
        entry["sublabels"] = subs
    if fallback:
        entry["evidence"]["fallback"] = fallback
    if not structure_known:
        entry["evidence"]["structure_unknown"] = "no items given: chosen from text signals only"
    if desc.get("zones") is not None:
        entry["zones"] = desc["zones"]
    elif feats["zones"]:
        entry["zones"] = len({str(byid[n].get("zone")) for n in kept if byid[n].get("zone")})
    return entry


def merge_plan(build_dir, entry: dict) -> Path:
    """Write or replace this figure's entry in <build>/diagram_plan.json (other figures untouched)."""
    bd = Path(build_dir)
    bd.mkdir(parents=True, exist_ok=True)
    p = bd / "diagram_plan.json"
    doc = {"schema": 1, "figures": []}
    if p.exists():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("figures"), list):
                doc = old
                doc.setdefault("schema", 1)
        except (ValueError, OSError):
            pass
    figs = [f for f in doc["figures"] if isinstance(f, dict)]
    for i, f in enumerate(figs):
        if f.get("id") == entry["id"]:
            figs[i] = entry
            break
    else:
        figs.append(entry)
    doc["figures"] = figs
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(p))
    return p


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return EXIT_PASS if argv else EXIT_ERROR
    src = Path(argv[0])
    try:
        desc = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.stderr.write(f"[coordn8:pick_diagram] ERROR: cannot read {src}: {e}\n")
        return EXIT_ERROR
    if not isinstance(desc, dict):
        sys.stderr.write("[coordn8:pick_diagram] ERROR: description must be a JSON object\n")
        return EXIT_ERROR
    try:
        entry = plan_figure(desc, catalog=_arg(argv, "--catalog"), figure_id=_arg(argv, "--figure"),
                            detail=_arg(argv, "--detail"), audience=_arg(argv, "--audience"))
    except (OSError, ValueError, KeyError) as e:
        sys.stderr.write(f"[coordn8:pick_diagram] ERROR: {type(e).__name__}: {e}\n")
        return EXIT_ERROR
    build = _arg(argv, "--build")
    if build:
        p = merge_plan(build, entry)
        sys.stderr.write(f"[coordn8:pick_diagram] wrote {entry['id']} -> {p}\n")
    if "--json" in argv:
        print(json.dumps(entry, indent=1, ensure_ascii=False))
    else:
        kind = f"invented '{entry['type']}'" if entry["invented"] else entry["type"]
        print(f"{entry['id']}: {kind} (layout {entry['layout']}) for behaviour '{entry['behaviour']}'"
              + (f", runner-up '{entry['runner_up']['label']}'" if entry["runner_up"] else ""))
        if entry["tie_breaker"] and entry["tie_breaker"].get("applied"):
            print(f"  tie-breaker: {entry['tie_breaker']['why']}")
        for r in entry["rejected"]:
            print(f"  rejected {r['type']}: {r['why']}")
        print(f"  nodes {entry['nodes']}/{entry['source_nodes']}, edges {entry['edges']}, detail {entry['detail']}, "
              f"names {entry['audience_names']}")
        for c in entry["cuts"]:
            print(f"  cut: {c}")
    return EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
