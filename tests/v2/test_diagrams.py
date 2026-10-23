#!/usr/bin/env python3
"""v2 suite: diagram type selection (B1), complexity budget (B2), detail vs audience dials (C5),
freedom doctrine for diagrams (H1).

Hermetic: every write goes to a temp build dir; nothing touches the user's home.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

CATALOG = ROOT / "data" / "diagram_types.json"
PICK = ROOT / "scripts" / "design" / "pick_diagram.py"
BUDGET = "scripts/gates/check_diagram_budget.py"
PLAN = "scripts/gates/check_diagram_plan.py"


sys.dont_write_bytecode = True  # leave nothing on disk, not even __pycache__


def senv(tmp: Path) -> dict:
    e = sandbox_env(tmp)
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    return e


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gate_json(cp) -> dict:
    for line in (cp.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def chain(n: int, prefix: str = "step") -> dict:
    items = [{"id": f"{prefix}{i}", "label": f"{prefix}_{i}"} for i in range(1, n + 1)]
    rels = [{"from": f"{prefix}{i}", "to": f"{prefix}{i + 1}"} for i in range(1, n)]
    return {"items": items, "relations": rels}


# ---- the 12 fixture descriptions: (name, description, expected type, allowed alternatives) ----
FIXTURES = [
    ("request/response over time -> sequence", {
        "text": "The browser sends a login request to the API, which calls the auth service; the auth "
                "service returns a token and the API replies to the browser.",
        "has_time_axis": True,
        "items": [{"id": "browser", "kind": "actor"}, {"id": "api", "kind": "actor"},
                  {"id": "auth", "label": "auth service", "kind": "actor"}],
        "relations": [{"from": "browser", "to": "api", "kind": "request", "label": "POST /login"},
                      {"from": "api", "to": "auth", "kind": "call", "label": "verify"},
                      {"from": "auth", "to": "api", "kind": "return", "label": "token"},
                      {"from": "api", "to": "browser", "kind": "response", "label": "200 + token"}]},
     "sequence", []),
    ("one subject's phases -> state machine", {
        "text": "An order moves from pending to paid, then shipped and delivered; a payment timeout "
                "cancels it, and a failed delivery retries.",
        "items": [{"id": "pending", "kind": "state"}, {"id": "paid", "kind": "state"},
                  {"id": "shipped", "kind": "state"}, {"id": "delivered", "kind": "state"},
                  {"id": "cancelled", "kind": "state"}, {"id": "failed_delivery", "kind": "state"}],
        "relations": [{"from": "pending", "to": "paid", "kind": "transition", "label": "pay"},
                      {"from": "pending", "to": "cancelled", "kind": "transition", "label": "timeout"},
                      {"from": "paid", "to": "shipped", "kind": "transition", "label": "ship"},
                      {"from": "shipped", "to": "delivered", "kind": "transition", "label": "arrive"},
                      {"from": "shipped", "to": "failed_delivery", "kind": "transition", "label": "miss"},
                      {"from": "failed_delivery", "to": "shipped", "kind": "transition", "label": "retry"}]},
     "state-machine", ["lifecycle-map"]),
    ("decision branches -> flowchart / decision tree", {
        "text": "Decide whether a refund is approved: if the order is within 30 days and unused, approve; "
                "otherwise, if the item is damaged, offer a replacement; else reject.",
        "items": [{"id": "within_30", "kind": "decision"}, {"id": "damaged", "kind": "decision"},
                  {"id": "approve", "kind": "terminal"}, {"id": "replace", "kind": "terminal"},
                  {"id": "reject", "kind": "terminal"}],
        "relations": [{"from": "within_30", "to": "approve", "when": "yes"},
                      {"from": "within_30", "to": "damaged", "when": "no"},
                      {"from": "damaged", "to": "replace", "when": "yes"},
                      {"from": "damaged", "to": "reject", "when": "no"}]},
     "flowchart", ["decision-tree"]),
    ("many sources one sink -> fan-in / data flow", {
        "text": "Five services send their logs into one collector, which becomes the bottleneck when "
                "traffic spikes.",
        "items": ["checkout", "search", "payments", "catalog", "accounts", "collector"],
        "relations": [{"from": s, "to": "collector", "kind": "flow"}
                      for s in ("checkout", "search", "payments", "catalog", "accounts")]},
     "data-flow", ["bottleneck-flow", "hub-and-spoke"]),
    ("2x2 trade-off -> quadrant", {
        "text": "Plot each feature by effort against impact to see which to prioritise first — a 2x2 "
                "trade-off.",
        "items": [{"id": "sso", "attrs": {"effort": 8, "impact": 9}},
                  {"id": "dark_mode", "attrs": {"effort": 2, "impact": 3}},
                  {"id": "export", "attrs": {"effort": 3, "impact": 7}},
                  {"id": "audit_log", "attrs": {"effort": 6, "impact": 5}}]},
     "quadrant", ["scatter"]),
    ("quantity split -> sankey / waterfall", {
        "text": "The 12,000 CI minutes budget splits across build, test and deploy; test splits further "
                "into unit and integration.",
        "items": ["budget", "build", "test", "deploy", "unit", "integration"],
        "relations": [{"from": "budget", "to": "build", "value": 3000},
                      {"from": "budget", "to": "test", "value": 7000},
                      {"from": "budget", "to": "deploy", "value": 2000},
                      {"from": "test", "to": "unit", "value": 2500},
                      {"from": "test", "to": "integration", "value": 4500}]},
     "sankey", ["waterfall"]),
    ("hierarchy -> tree", {
        "text": "The documentation taxonomy: the root has three sections, each with child pages.",
        "items": ["docs", "guides", "reference", "faq", "install", "upgrade", "api", "cli"],
        "relations": [{"from": "docs", "to": c, "kind": "child"} for c in ("guides", "reference", "faq")]
        + [{"from": "guides", "to": "install", "kind": "child"}, {"from": "guides", "to": "upgrade", "kind": "child"},
           {"from": "reference", "to": "api", "kind": "child"}, {"from": "reference", "to": "cli", "kind": "child"}]},
     "tree", ["org-chart", "nested", "mind-map"]),
    ("layered controls -> layers", {
        "text": "Defence in depth: input validation, authentication, authorisation, rate limiting and "
                "audit logging are layered controls; each layer catches what the previous one missed.",
        "items": [{"id": "validation", "layer": 1}, {"id": "authentication", "layer": 2},
                  {"id": "authorisation", "layer": 3}, {"id": "rate_limiting", "layer": 4},
                  {"id": "audit_logging", "layer": 5}]},
     "layers", ["control-catalog", "compensating-layers"]),
    ("3 options x 4 attributes -> table (tie-breaker)", {
        "text": "Compare Postgres, MySQL and SQLite on cost, speed, scale and ease of operation.",
        "items": [{"id": "postgres", "attrs": {"cost": 0, "speed": 8, "scale": 9, "ops": 6}},
                  {"id": "mysql", "attrs": {"cost": 0, "speed": 8, "scale": 8, "ops": 7}},
                  {"id": "sqlite", "attrs": {"cost": 0, "speed": 7, "scale": 3, "ops": 10}}]},
     "table", []),
    ("change over time -> line / timeline", {
        "text": "Weekly signups grew from 120 to 480 over the last 8 weeks.",
        "has_time_axis": True,
        "items": [{"id": f"w{i}", "time": f"week {i}", "value": v}
                  for i, v in enumerate([120, 150, 190, 230, 280, 330, 410, 480], start=1)]},
     "line", ["timeline"]),
    ("dependency -> dependency graph", {
        "text": "Which packages depend on the shared core library? api and worker both depend on core "
                "and auth; auth depends on core.",
        "items": ["api", "worker", "auth", "core"],
        "relations": [{"from": "api", "to": "core", "kind": "depends"},
                      {"from": "api", "to": "auth", "kind": "depends"},
                      {"from": "worker", "to": "core", "kind": "depends"},
                      {"from": "worker", "to": "auth", "kind": "depends"},
                      {"from": "auth", "to": "core", "kind": "depends"}]},
     "dependency-graph", []),
    ("trust boundary -> architecture with zones", {
        "text": "Traffic from the public internet enters through the gateway in the DMZ; only the gateway "
                "may cross the trust boundary into the private zone where the API and database live.",
        "items": [{"id": "internet", "zone": "public"}, {"id": "gateway", "zone": "dmz"},
                  {"id": "api", "zone": "private"}, {"id": "database", "zone": "private"}],
        "relations": [{"from": "internet", "to": "gateway"}, {"from": "gateway", "to": "api"},
                      {"from": "api", "to": "database"}]},
     "architecture", ["boundary-map", "deployment"]),
]


# ---- generalisation: descriptions beyond the 12 fixtures, text only (no structure given) ----
TEXT_ONLY = [
    ("root causes of one incident", "What were the root causes of the checkout latency incident? Contributing "
     "factors by category.", ["fishbone"]),
    ("overlap of two sets", "Where do the skills of designers and engineers overlap? The intersection is the "
     "sweet spot.", ["venn"]),
    ("a person's experience", "The customer journey: how a trial user feels at each touchpoint over the first "
     "week.", ["journey-map", "service-blueprint"]),
    ("work in progress by state", "The kanban board: what is in progress, what is blocked, WIP limits per column.",
     ["kanban", "table"]),
    ("tasks on a schedule", "Project roadmap: tasks with start date and end date across three sprints, the "
     "critical path.", ["gantt", "timeline"]),
    ("entities and cardinality", "Entities and cardinality: a customer has many orders, an order has many line "
     "items (one-to-many).", ["er", "db-schema"]),
    ("reinforcing loop", "The flywheel: more users bring more data, which improves the model, which brings more "
     "users — a reinforcing loop.", ["loop", "causal-loop", "cycle"]),
    ("signed bridge between totals", "How the 2026 budget bridges from last year: hires add, attrition subtracts, "
     "net change.", ["waterfall"]),
    ("share of a whole", "Which share of the bundle size does each dependency account for? Percent of the total.",
     ["treemap", "bar"]),
    ("distribution", "Distribution of request latency: median, p99 and outliers.",
     ["histogram", "box-plot", "beeswarm"]),
    ("structural migration", "Migration: the system before and after moving the queue out of the monolith.",
     ["architecture-delta"]),
    ("two policies diverge", "Two policies, same request, different outcome: where do the traces first diverge?",
     ["paired-trace"]),
    ("conversion drop-off", "Signup funnel: visit, sign up, activate, pay — the conversion drop-off at each "
     "stage.", ["funnel"]),
    ("where software runs", "Where the software runs: three hosts in two regions, replicas of the API, the "
     "database primary.", ["deployment"]),
    ("ownership", "Who owns what: the platform team reports to infra; escalation goes to on-call.",
     ["org-chart"]),
    ("staged pipeline", "The request pipeline: parse, validate, enrich, then store — each step hands off to the "
     "next.", ["process", "data-flow"]),
]


def main() -> int:
    s = Suite("diagrams")
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    pick = load_module(PICK, "c8_pick_diagram_test")
    types = cat.get("types", [])
    ids = [t["id"] for t in types]
    idset = set(ids)
    behaviours = cat.get("behaviours", [])

    # ---------------- catalog (B1, diagram-02) ----------------
    s.check("catalog: schema 1 with behaviours and types", cat.get("schema") == 1 and types and behaviours,
            str(cat.get("schema")))
    s.check("catalog: at least 45 diagram types (>= 40 required)", len(types) >= 45, f"{len(types)} types")
    s.check("catalog: type ids are unique", len(ids) == len(idset), "duplicate ids")
    dangling = dangling_pointers(cat)
    s.check("catalog: every not_this pointer resolves to a real, different type", not dangling, str(dangling[:5]))
    seeded = json.loads(json.dumps(cat))
    seeded["types"][0]["not_this"].append({"type": "no-such-neighbour", "when": "seeded"})
    s.check("catalog negative: a seeded dangling not_this pointer is caught by the same check",
            dangling_pointers(seeded) == [(seeded["types"][0]["id"], "no-such-neighbour")], str(dangling_pointers(seeded)))
    no_ptr = [t["id"] for t in types if not t.get("not_this")]
    s.check("catalog: every type carries at least one not-this -> neighbour pointer", not no_ptr, str(no_ptr))
    s.check("catalog: every not_this pointer says when the neighbour is better",
            all(str(nt.get("when") or "").strip() for t in types for nt in t.get("not_this", [])), "")
    bad_shape = [t["id"] for t in types if not (
        t.get("use_when") and isinstance(t.get("fields"), list) and t["fields"]
        and t.get("layout") in ("layered", "radial", "grid", "timeline", "matrix", "freeform")
        and isinstance(t.get("table_equivalent"), bool)
        and all(isinstance(t.get("budget", {}).get(k), int) for k in ("nodes", "edges", "accents", "callouts")))]
    s.check("catalog: every type has use_when, fields, layout, table_equivalent and a 4-key budget",
            not bad_shape, str(bad_shape[:5]))
    s.check("catalog: at least 12 behaviours, each with signals and suggested types",
            len(behaviours) >= 12 and all(b.get("signals") and b.get("suggest") for b in behaviours),
            f"{len(behaviours)} behaviours")
    unres = [(b["id"], x) for b in behaviours for x in b["suggest"] if x not in idset]
    s.check("catalog: every behaviour suggestion resolves to a catalog type", not unres, str(unres[:5]))
    feats = set(cat.get("features", {}))
    unknown = [(t["id"], k) for t in types for k in list(t.get("fit", {})) + t.get("requires", []) if k not in feats] + \
              [(b["id"], k) for b in behaviours for k in b.get("structure", {}) if k not in feats]
    s.check("catalog: every scoring feature used by a behaviour or type is declared", not unknown, str(unknown[:5]))
    must_b = {"fan-in", "staged-process", "decision-branching", "policy-divergence", "trust-boundaries",
              "layered-controls", "decomposition", "lifecycle", "messages-over-time", "quantity-split-merge",
              "attribute-comparison", "change-over-time", "hierarchy", "dependency", "proportion"}
    s.check("catalog: the 15 named behaviours are all present",
            must_b <= {b["id"] for b in behaviours}, str(sorted(must_b - {b["id"] for b in behaviours})))
    domain = [i for i in ids if i.startswith("dp-") or "medallion" in i]
    s.check("catalog: domain-specific data-platform types are dropped or generalised", not domain, str(domain))
    s.check("catalog: global budget is 9 nodes / 12 edges / 2 accents / 2 callouts",
            cat.get("global_budget") == {"nodes": 9, "edges": 12, "accents": 2, "callouts": 2},
            str(cat.get("global_budget")))
    s.check("catalog: per-type overrides exist (sequence 5 lifelines, scatter 30 points)",
            next(t for t in types if t["id"] == "sequence")["budget"]["nodes"] == 5
            and next(t for t in types if t["id"] == "scatter")["budget"]["nodes"] == 30, "")
    s.check("catalog: a table type exists and at least 5 types are flagged table_equivalent",
            "table" in idset and sum(1 for t in types if t["table_equivalent"]) >= 5, "")

    # ---------------- selection on 12 fixtures (B1, diagram-01) ----------------
    plans = {}
    for name, desc, expected, alts in FIXTURES:
        e = pick.plan_figure(desc, catalog=cat, figure_id="fx")
        plans[name] = e
        ok = e["type"] == expected or e["type"] in alts
        s.check(f"fixture: {name}", ok, f"got {e['type']} (behaviour {e['behaviour_id']}, scores {e['type_scores']})")
    s.check("selection: every fixture plan records a dominant behaviour and a scored runner-up",
            all(p["behaviour_id"] and p["runner_up"] and p["runner_up"]["id"] != p["behaviour_id"]
                for p in plans.values()), "")
    s.check("selection: every fixture plan records the evidence (signals / structural features) for its behaviour",
            all(p["evidence"]["signals"] or p["evidence"]["structure"] for p in plans.values()),
            str([n for n, p in plans.items() if not (p["evidence"]["signals"] or p["evidence"]["structure"])]))
    s.check("plans: every fixture lists >= 2 rejected options, each with a reason",
            all(len(p["rejected"]) >= 2 and all(r["type"] and r["why"].strip() for r in p["rejected"])
                for p in plans.values()), str({n: len(p["rejected"]) for n, p in plans.items()}))
    s.check("plans: rejected options never include the chosen type",
            all(all(r["type"] != p["type"] for r in p["rejected"]) for p in plans.values()), "")
    seq = plans[FIXTURES[0][0]]
    no_t = {"text": "Weekly signups grew over the last 8 weeks.",
            "items": [{"id": f"w{i}", "value": v} for i, v in enumerate([120, 190, 280, 480], start=1)]}
    nt = pick.plan_figure(no_t, catalog=cat)
    s.check("plans: a rejection with an unmet requirement says what the content lacks",
            any(r["type"] == "line" and "needs what this content does not have" in r["why"]
                and "ordered in time" in r["why"] for r in nt["rejected"]), str(nt["rejected"]))
    s.check("plans: the table is always weighed — rejected with a reason when not chosen",
            all(p["type"] == "table" or any(r["type"] == "table" for r in p["rejected"]) for p in plans.values()),
            "")
    tr = plans[FIXTURES[-1][0]]
    s.check("selection: trust boundary plan records the zones variant", tr.get("variant") == "zones",
            str(tr.get("variant")))
    s.check("selection: sequence plan uses the timeline layout", seq["layout"] == "timeline", seq["layout"])
    again = pick.plan_figure(FIXTURES[2][1], catalog=cat, figure_id="fx")
    s.check("selection: deterministic — the same description yields an identical plan entry",
            json.dumps(again, sort_keys=True) == json.dumps(plans[FIXTURES[2][0]], sort_keys=True), "")
    none = pick.plan_figure({"text": "zzz", "items": ["a", "b"], "relations": [{"from": "a", "to": "b"}]},
                            catalog=cat, figure_id="fx")
    s.check("selection: no matching signal falls back by structure and says so",
            none["type"] and none["behaviour_id"] == "topology" and "fallback" in none["evidence"],
            str(none["evidence"]))

    misses = []
    for name, desc, expected, alts in FIXTURES:
        e1 = pick.plan_figure({"text": desc["text"]}, catalog=cat)
        e2 = pick.plan_figure({k: v for k, v in desc.items() if k != "text"}, catalog=cat)
        for tag, e in (("text-only", e1), ("structure-only", e2)):
            if not (e["type"] == expected or e["type"] in alts):
                misses.append(f"{name} [{tag}] -> {e['type']}")
    s.check("robust: each fixture maps to the same sensible type from its text alone AND from its structure alone",
            not misses, "; ".join(misses))
    gen_miss = []
    for name, text, allowed in TEXT_ONLY:
        e = pick.plan_figure({"text": text}, catalog=cat)
        if e["type"] not in allowed:
            gen_miss.append(f"{name} -> {e['type']} ({e['behaviour_id']})")
    s.check(f"robust: {len(TEXT_ONLY)} further descriptions beyond the fixtures map to sensible types",
            not gen_miss, "; ".join(gen_miss))
    s.check("robust: with no items given, the plan says the structure was unknown (requirements not penalised)",
            "structure_unknown" in pick.plan_figure({"text": TEXT_ONLY[0][1]}, catalog=cat)["evidence"], "")

    # ---------------- table tie-breaker (diagram-03) ----------------
    tb = plans[FIXTURES[8][0]]
    s.check("tie-breaker: 3 options x 4 attributes chooses the table and says so",
            tb["type"] == "table" and tb["tie_breaker"] and tb["tie_breaker"]["applied"]
            and "table" in tb["tie_breaker"]["why"], str(tb["tie_breaker"]))
    rel = dict(FIXTURES[8][1])
    rel["relations"] = [{"from": "postgres", "to": "mysql", "kind": "replicates"},
                        {"from": "mysql", "to": "sqlite", "kind": "replicates"}]
    s.check("tie-breaker negative: the same items with relations do not become a table",
            pick.plan_figure(rel, catalog=cat)["type"] != "table", "")
    bub = {"text": "Does latency correlate with payload size? Each service plotted with its request count.",
           "items": [{"id": f"svc{i}", "attrs": {"latency": 10 * i, "payload": 3 * i + 1, "requests": 100 + i}}
                     for i in range(1, 7)]}
    be = pick.plan_figure(bub, catalog=cat)
    s.check("tie-breaker: 3 numeric attributes where the shape is the message is NOT forced into a table "
            "(and the reason is recorded)",
            be["type"] != "table" and be["tie_breaker"] and be["tie_breaker"]["applied"] is False,
            f"{be['type']} {be['tie_breaker']}")
    acc = {"text": "Who can read or write each bucket: the permissions per role.",
           "items": [{"id": r, "kind": "role", "attrs": {"reports": a, "exports": b}}
                     for r, a, b in (("analyst", "read", "none"), ("engineer", "write", "read"),
                                     ("admin", "write", "write"))]}
    pe = pick.plan_figure(acc, catalog=cat)
    tix = {t["id"]: t for t in types}
    s.check("tie-breaker: a table-equivalent pick whose content fits 3 columns becomes a table (and says so)",
            pe["type"] == "table" and pe["tie_breaker"]["rule"] == "table-equivalent"
            and tix[pe["tie_breaker"]["replaced"]]["table_equivalent"],
            f"{pe['type']} {pe['tie_breaker']} {pe['type_scores']}")

    # ---------------- detail and audience dials (C5, diagram-13) ----------------
    ten = chain(10)
    hi = pick.plan_figure(ten, catalog=cat, detail="high", audience="plain")
    lo = pick.plan_figure(ten, catalog=cat, detail="low", audience="plain")
    s.check("dials: low detail halves the nodes (10 -> 5)", hi["nodes"] == 10 and lo["nodes"] == 5,
            f"high {hi['nodes']} low {lo['nodes']}")
    s.check("dials: low detail never renames a kept node",
            all(lo["names"][k] == hi["names"][k] for k in lo["kept"]), "")
    s.check("dials: low detail records what it cut", lo["cuts"] and "detail=low" in lo["cuts"][0], str(lo["cuts"]))
    fourteen = chain(14)
    med = pick.plan_figure(fourteen, catalog=cat, detail="medium")
    s.check("dials: medium detail keeps nodes up to the chosen type's budget",
            med["nodes"] == min(14, med["budget"]["nodes"]), f"{med['nodes']} vs {med['budget']['nodes']}")
    imp = chain(6)
    imp["items"][5]["importance"] = 10
    ie = pick.plan_figure(imp, catalog=cat, detail="low")
    s.check("dials: detail keeps the most important node first", "step6" in ie["kept"], str(ie["kept"]))
    plain = pick.plan_figure(ten, catalog=cat, detail="high", audience="plain")
    field = pick.plan_figure(ten, catalog=cat, detail="high", audience="field")
    s.check("dials: audience changes names without dropping any node",
            plain["kept"] == field["kept"] and plain["nodes"] == field["nodes"]
            and any(plain["names"][k] != field["names"][k] for k in plain["kept"]),
            f"{plain['names'].get('step1')} vs {field['names'].get('step1')}")
    s.check("dials: plain names are human words, field names are the exact field",
            plain["names"]["step1"] == "Step 1" and field["names"]["step1"] == "step_1",
            f"{plain['names']['step1']!r} / {field['names']['step1']!r}")
    lo_field = pick.plan_figure(ten, catalog=cat, detail="low", audience="field")
    s.check("dials: the two dials are independent (low+field keeps the same nodes as low+plain)",
            lo_field["kept"] == lo["kept"] and all(lo_field["names"][k] == field["names"][k] for k in lo_field["kept"]),
            "")
    mixed = pick.plan_figure(ten, catalog=cat, detail="high", audience="mixed")
    s.check("dials: mixed audience keeps the plain name and shows the field as a sublabel",
            mixed["names"]["step1"] == "Step 1" and mixed.get("sublabels", {}).get("step1") == "step_1", "")
    s.check("dials: plan entry records detail and audience_names",
            lo["detail"] == "low" and field["audience_names"] == "field", "")

    # ---------------- budget gate (B2, diagram-04) ----------------
    budget = load_module(ROOT / BUDGET, "c8_budget_test")
    with tmpdir() as td:
        t = Path(td)
        env = senv(t)
        f14 = pick.plan_figure(fourteen, catalog=cat, figure_id="flow14", detail="high")
        (t / "f14.json").write_text(json.dumps({"schema": 1, "figures": [f14]}), encoding="utf-8")
        cp = run(BUDGET, [t / "f14.json"], env=env, timeout=60)
        g = gate_json(cp)
        s.check("budget: a 14-node flow is flagged REVIEW (exit 1)", cp.returncode == 1 and g.get("status") == "REVIEW",
                f"rc {cp.returncode} {cp.stdout[-300:]} {cp.stderr[-200:]}")
        fig = (g.get("figures") or [{}])[0]
        s.check("budget: the advice is 'split into overview + detail'", fig.get("advice") == "split into overview + detail",
                str(fig.get("advice")))
        sp = fig.get("proposed_split") or {}
        det = sp.get("details") or []
        members = [n for d in det for n in d.get("nodes", [])]
        s.check("budget: the proposed split covers all 14 nodes once, each detail within the cap",
                sorted(members) == sorted(f14["kept"]) and all(d["node_count"] <= 9 for d in det) and len(det) >= 2,
                str([d.get("node_count") for d in det]))
        s.check("budget: the proposed split's overview fits the budget and links the groups",
                sp.get("overview", {}).get("within_budget") is True and sp["overview"]["edges"], str(sp.get("overview")))
        s.check("budget: clusters follow connectivity (each detail of a chain is one connected run)",
                all(_connected(d["nodes"], f14["edges_kept"]) for d in det), str([d["nodes"] for d in det]))
        nine = {"id": "ok9", "type": "flowchart", "nodes": 9, "edges": 12, "accents": 2, "callouts": 2}
        (t / "f9.json").write_text(json.dumps(nine), encoding="utf-8")
        cp9 = run(BUDGET, [t / "f9.json"], env=env, timeout=60)
        s.check("budget: 9 nodes / 12 edges / 2 accents / 2 callouts passes (exit 0)", cp9.returncode == 0,
                cp9.stdout[-300:])
        g_, per, faithful = budget.load_overrides()
        r = budget.check_figure({"id": "a", "type": "flowchart", "nodes": 5, "edges": 4, "accents": 3}, g_, per, faithful)
        s.check("budget: 3 accents is flagged with accent advice (no split)",
                "accents" in r["over"] and "accent_advice" in r and "proposed_split" not in r, str(r["over"]))
        r = budget.check_figure({"id": "c", "nodes": 4, "edges": 3, "callouts": 3}, g_, per, faithful)
        s.check("budget: 3 callouts is flagged", "callouts" in r["over"], str(r["over"]))
        r = budget.check_figure({"id": "s", "type": "scatter", "nodes": 20, "edges": 0}, g_, per, faithful)
        s.check("budget: per-type override — a 20-point scatter passes (cap 30)", not r["over"], str(r))
        r = budget.check_figure({"id": "q", "type": "sequence", "nodes": 7, "edges": 6}, g_, per, faithful)
        s.check("budget: per-type override — a 7-lifeline sequence is flagged (cap 5)",
                r["over"].get("nodes") == [7, 5], str(r["over"]))
        r = budget.check_figure({"id": "x", "type": "sequence", "nodes": 4, "edges": 4, "fragments": 3}, g_, per, faithful)
        s.check("budget: extra per-type limits are checked when the figure reports them (fragments)",
                "fragments" in r["over"], str(r["over"]))
        r = budget.check_figure({"id": "z", "type": "freeform-swirl-never-listed", "nodes": 8, "edges": 10}, g_, per, faithful)
        s.check("budget: a type the catalog does not list gets global caps and passes when within them",
                not r["over"] and r["caps_from"] == "global", str(r))
        r = budget.check_figure({"id": "inv", "type": "flowchart", "invented": {"name": "tidal map", "why": "x"},
                                 "nodes": 14, "edges": 13}, g_, per, faithful)
        s.check("budget: an invented form over the global caps is flagged by outcome, with a split",
                r["over"].get("nodes") == [14, 9] and "proposed_split" in r and "invented" in r["caps_from"], str(r))
        r = budget.check_figure({"id": "fz", "type": "architecture", "nodes": 20, "edges": 24, "detail": "high",
                                 "zones": 3}, g_, per, faithful)
        r2 = budget.check_figure({"id": "fz2", "type": "architecture", "nodes": 30, "edges": 24, "detail": "high",
                                  "zones": 3}, g_, per, faithful)
        s.check("budget: a zoned faithful redraw may hold 20 nodes; 30 is still flagged",
                not r["over"] and r2["over"].get("nodes") == [30, 24], f"{r['over']} / {r2['over']}")
        r = budget.check_figure({"id": "cnt", "nodes": 20, "edges": 5}, g_, per, faithful)
        s.check("budget: a counts-only figure gets a numeric split proposal",
                r["proposed_split"]["method"].startswith("count-only") and r["proposed_split"]["overview"]["nodes"] >= 3,
                str(r["proposed_split"]))
        cp0 = run(BUDGET, [t / "missing.json"], env=env, timeout=60)
        s.check("budget: missing input is SKIPPED (exit 2), not a pass", cp0.returncode == 2, str(cp0.returncode))

    # ---------------- plan gate (B1 "no figure without a plan entry") ----------------
    with tmpdir() as td:
        t = Path(td)
        env = senv(t)
        build = t / "_build"
        d1 = dict(FIXTURES[2][1])
        d1["id"] = "fig-refund"
        cpj = run("scripts/design/pick_diagram.py", [_w(t / "d1.json", d1), "--build", build, "--json"],
                  env=env, timeout=60)
        out = json.loads(cpj.stdout) if cpj.returncode == 0 and cpj.stdout.strip().startswith("{") else {}
        s.check("cli: pick_diagram --build writes diagram_plan.json and --json prints the entry",
                (build / "diagram_plan.json").exists() and out.get("id") == "fig-refund",
                f"rc {cpj.returncode} {cpj.stderr[-300:]}")
        d2 = dict(FIXTURES[0][1])
        run("scripts/design/pick_diagram.py", [_w(t / "d2.json", d2), "--build", build, "--figure", "fig-login"],
            env=env, timeout=60)
        run("scripts/design/pick_diagram.py", [t / "d1.json", "--build", build, "--audience", "field"],
            env=env, timeout=60)
        (t / "bad.json").write_text("{not json", encoding="utf-8")
        cpb = run("scripts/design/pick_diagram.py", [t / "bad.json", "--build", build], env=env, timeout=60)
        s.check("cli: an unreadable description exits 3 (ERROR)", cpb.returncode == 3, str(cpb.returncode))
        doc = json.loads((build / "diagram_plan.json").read_text(encoding="utf-8"))
        fids = [f["id"] for f in doc["figures"]]
        s.check("cli: entries merge by figure id (two figures, re-run replaces instead of duplicating)",
                fids == ["fig-refund", "fig-login"] and doc["figures"][0]["audience_names"] == "field", str(fids))
        s.check("plan entry carries the SPEC 3.4 fields (id, behaviour, type, invented, rejected, nodes, edges, "
                "cuts, audience_names)",
                all(k in doc["figures"][0] for k in ("id", "behaviour", "type", "invented", "rejected", "nodes",
                                                     "edges", "cuts", "audience_names")) and doc["schema"] == 1, "")
        (build / "page.html").write_text('<figure data-figure="fig-refund"><svg></svg></figure>'
                                         '<svg data-figure=\'fig-login\'></svg>', encoding="utf-8")
        cpp = run(PLAN, [build], env=env, timeout=60)
        s.check("plan gate: every drawn figure planned -> PASS (exit 0)", cpp.returncode == 0,
                f"{cpp.returncode} {cpp.stdout[-400:]}")
        (build / "scene3.svg").write_text('<svg data-figure="fig-unplanned"></svg>', encoding="utf-8")
        cpp = run(PLAN, [build], env=env, timeout=60)
        g = gate_json(cpp)
        s.check("plan gate: a drawn figure with no plan entry fails (REVIEW, exit 1) and is named",
                cpp.returncode == 1 and any(f["figure"] == "fig-unplanned" and "no plan entry" in f["problems"][0]
                                            for f in g.get("findings", [])), cpp.stdout[-400:])
        (build / "scene3.svg").unlink()
        (build / "scene_manifest.json").write_text(json.dumps({"scenes": [{"id": "s1", "figure": "fig-login"},
                                                                          {"id": "s2", "figures": ["fig-ghost"]}]}),
                                                   encoding="utf-8")
        cpp = run(PLAN, [build], env=env, timeout=60)
        g = gate_json(cpp)
        s.check("plan gate: figures named in a scene manifest are checked too",
                cpp.returncode == 1 and "fig-ghost" in g.get("drawn", []), str(g.get("drawn")))
        (build / "scene_manifest.json").unlink()
        doc["figures"][1]["rejected"] = doc["figures"][1]["rejected"][:1]
        (build / "diagram_plan.json").write_text(json.dumps(doc), encoding="utf-8")
        cpp = run(PLAN, [build], env=env, timeout=60)
        s.check("plan gate: an entry with fewer than 2 rejections is flagged",
                cpp.returncode == 1 and ">= 2 rejected" in cpp.stdout, cpp.stdout[-300:])
        (t / "empty").mkdir()
        cpp = run(PLAN, [t / "empty"], env=env, timeout=60)
        s.check("plan gate: no plan and nothing drawn is SKIPPED (exit 2)", cpp.returncode == 2, str(cpp.returncode))

    # ---------------- invented form passes both gates (H1) ----------------
    with tmpdir() as td:
        t = Path(td)
        env = senv(t)
        build = t / "_build"
        inv = dict(FIXTURES[3][1])
        inv["id"] = "fig-tide"
        inv["invented"] = {"name": "tide-pool map", "why": "the logs pool and drain like a tide; the "
                           "collector's rise and fall is the story", "layout": "freeform"}
        e = pick.plan_figure(inv, catalog=cat)
        s.check("invented: the caller's invented form is accepted as the type, with its reason",
                e["type"] == "tide-pool map" and e["invented"]["why"] and e["layout"] == "freeform", str(e["type"]))
        s.check("invented: the plan still lists >= 2 catalog options considered, with reasons",
                len(e["rejected"]) >= 2 and all("invented" in r["why"] or r["type"] == "table" for r in e["rejected"]),
                str(e["rejected"][:2]))
        pick.merge_plan(build, e)
        (build / "page.html").write_text('<div data-figure="fig-tide"></div>', encoding="utf-8")
        cp1 = run(PLAN, [build], env=env, timeout=60)
        cp2 = run(BUDGET, [build], env=env, timeout=60)
        s.check("invented: an invented form with a reason passes the plan gate", cp1.returncode == 0, cp1.stdout[-300:])
        s.check("invented: an invented form within the global caps passes the budget gate", cp2.returncode == 0,
                cp2.stdout[-300:])
        noreason = pick.plan_figure(dict(inv, invented={"name": "tide-pool map", "why": ""}), catalog=cat)
        pick.merge_plan(build, noreason)
        cp3 = run(PLAN, [build], env=env, timeout=60)
        s.check("invented: an invented form without a reason is flagged by the plan gate",
                cp3.returncode == 1 and "no reason" in cp3.stdout, cp3.stdout[-300:])

    # ---------------- no gate references catalog membership (H1) ----------------
    banned = re.compile(r"not in (the )?catalog|unknown (diagram )?type|unlisted type|not a (known|catalog) type|"
                        r"must be (one of|in) the catalog", re.I)
    srcs = {p: (ROOT / p).read_text(encoding="utf-8") for p in (BUDGET, PLAN)}
    hits = {p: banned.findall(txt) for p, txt in srcs.items() if banned.search(txt)}
    s.check("freedom: no diagram gate fails anything for not being in the catalog (source scan)", not hits, str(hits))
    s.check("freedom: the plan gate never opens the type catalog",
            "diagram_types" not in srcs[PLAN], "plan gate reads the catalog")
    import inspect
    s.check("freedom: the budget gate uses the catalog only for optional per-type overrides",
            "catalog" not in inspect.getsource(budget.check_figure).lower()
            and 'source = "global"' in inspect.getsource(budget.caps_for), "")
    with tmpdir() as td:
        t = Path(td)
        env = senv(t)
        fake = {"schema": 1, "figures": [{"id": "fig-new", "type": "braided-ribbon (never catalogued)",
                                          "invented": None, "rejected": [{"type": "sankey", "why": "a"},
                                                                         {"type": "table", "why": "b"}],
                                          "nodes": 6, "edges": 5}]}
        (t / "diagram_plan.json").write_text(json.dumps(fake), encoding="utf-8")
        (t / "p.html").write_text('<svg data-figure="fig-new"></svg>', encoding="utf-8")
        a = run(PLAN, [t], env=env, timeout=60)
        b = run(BUDGET, [t], env=env, timeout=60)
        s.check("freedom: a type no catalog lists passes both gates when its outcome is fine",
                a.returncode == 0 and b.returncode == 0, f"{a.stdout[-200:]} {b.stdout[-200:]}")
        broken = t / "broken_catalog.json"
        broken.write_text("{}", encoding="utf-8")
        c = run(BUDGET, [t, "--catalog", broken], env=env, timeout=60)
        s.check("freedom: the budget gate still works with an empty catalog (global caps)", c.returncode == 0,
                c.stdout[-200:])

    # ---------------- improvements over the reference method (verdict 'better') ----------------
    s.check("better: selection is mechanical — behaviour and type scores are recorded in every plan",
            all(p["behaviour_scores"] and p["type_scores"] for p in plans.values()), "")
    s.check("better: behaviours the reference routing table lacks are present (quantity split, comparison, "
            "change over time, hierarchy, dependency, proportion)",
            {"quantity-split-merge", "attribute-comparison", "change-over-time", "hierarchy", "dependency",
             "proportion"} <= {b["id"] for b in behaviours}, "")
    s.check("better: every type (not only some) has a not-this pointer, and every type is reachable from a behaviour",
            not no_ptr and all(t.get("behaviours") for t in types), str([t["id"] for t in types if not t.get("behaviours")]))
    return s.finish()


def dangling_pointers(cat: dict) -> list:
    idset = {t["id"] for t in cat.get("types", [])}
    return [(t["id"], nt.get("type")) for t in cat.get("types", []) for nt in t.get("not_this", [])
            if nt.get("type") not in idset or nt.get("type") == t["id"]]


def _connected(nodes, edges) -> bool:
    ns = set(nodes)
    if len(ns) <= 1:
        return True
    adj = {n: set() for n in ns}
    for a, b in edges:
        if a in ns and b in ns:
            adj[a].add(b)
            adj[b].add(a)
    start = next(iter(ns))
    seen, todo = set(), [start]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(adj[n] - seen)
    return seen == ns


def _w(path: Path, obj) -> Path:
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
