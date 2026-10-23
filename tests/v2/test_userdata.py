#!/usr/bin/env python3
"""User-data store + migrations: J2 (store side), J6, and the data layer of K1 / K3 / K5.

Hermetic: every check runs against a temp COORDN8_HOME; nothing is written outside it.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import ROOT, Suite, run, sandbox_env, tmpdir  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))

s = Suite("userdata")


def tree(home: Path, skip_backups: bool = True) -> dict:
    out = {}
    for p in sorted(home.rglob("*")):
        rel = p.relative_to(home)
        if skip_backups and rel.parts and rel.parts[0] == "backups":
            continue
        if p.is_file():
            out[str(rel)] = hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_dir():
            out[str(rel) + "/"] = "dir"
    return out


def fresh(env: dict):
    """(Re)import the modules so they see this sandbox's COORDN8_HOME."""
    os.environ.update(env)
    import lib.paths as paths
    import memory.store as store
    import memory.migrate as migrate
    importlib.reload(paths)
    importlib.reload(store)
    importlib.reload(migrate)
    return paths, store, migrate


real_home_store = Path.home() / ".coordn8"
real_before = tree(real_home_store) if real_home_store.exists() else None

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)
    paths, store, migrate = fresh(env)
    Store = store.Store
    H = Path(env["COORDN8_HOME"])

    # ---------------------------------------------------------------- basics
    st = Store()
    s.check("store: default home is the sandboxed COORDN8_HOME (never the real ~/.coordn8)",
            st.home == H, str(st.home))
    s.check("store: constructor creates only missing dirs, writes no file",
            (H / "memory").is_dir() and (H / "library").is_dir()
            and not [p for p in H.rglob("*") if p.is_file()], str(list(H.rglob("*"))))
    s.check("paths: USER_SCHEMA_VERSION is 2 and migrate.CURRENT follows it",
            paths.USER_SCHEMA_VERSION == 2 and migrate.CURRENT == 2, str(migrate.CURRENT))

    e = st.add("preference", "Use a warm, low-saturation palette for openings",
               scope="global", source_quote="I like warm muted openings", source="chat")
    s.check("store: add returns a full SPEC 3.7 entry with an m-<hex> id and ISO date",
            all(k in e for k in ("id", "type", "scope", "text", "source_quote", "source", "date",
                                 "confidence", "supersedes", "source_hash"))
            and e["id"].startswith("m-") and len(e["id"]) == 14 and "T" in e["date"], str(e))
    bad = []
    for kw in ({"type": "opinion", "text": "x"}, {"type": "fact", "text": "  "},
               {"type": "fact", "text": "x", "scope": "Project:Big"},
               {"type": "fact", "text": "x", "confidence": "certain"},
               {"type": "fact", "text": "x", "supersedes": "m-doesnotexist"}):
        try:
            st.add(**kw)
            bad.append(kw)
        except (ValueError, KeyError):
            pass
    s.check("store: rejects unknown type, empty text, bad scope, bad confidence, unknown supersedes",
            not bad, str(bad))

    # ---- K1: global preference from project A is visible while working in project B
    a_scope, b_scope = store.project_scope("/work/Project_A"), store.project_scope("project-b")
    pa = st.add("preference", "Narration voice: calm and unhurried", scope="global",
                source_quote="(said while building project A)", source="chat",
                project_origin=a_scope)
    pa_local = st.add("decision", "Open on the request lifecycle", scope=a_scope)
    in_b = st.list(scope=b_scope)
    s.check("K1 a global preference stated in project A is listed when working in project B",
            any(x["id"] == pa["id"] for x in in_b), json.dumps([x["id"] for x in in_b]))
    s.check("K1 a project-A-scoped decision is NOT listed in project B (scopes stay separate)",
            all(x["id"] != pa_local["id"] for x in in_b)
            and any(x["id"] == pa_local["id"] for x in st.list(scope=a_scope)), "")
    s.check("K1 a fresh Store instance (a later session) reads the same entries back",
            [x["id"] for x in Store().list()] == [x["id"] for x in st.list()], "")

    # ---- K5: supersede keeps both; only the new is current; old line byte-identical
    before = (H / "memory/entries.jsonl").read_bytes()
    new = st.supersede(pa["id"], "Narration voice: brisk and energetic",
                       reason="owner asked for more energy in build 2", source="chat",
                       source_quote="make the voice punchier")
    after = (H / "memory/entries.jsonl").read_bytes()
    cur = [x["id"] for x in st.list(scope=b_scope)]
    every = [x["id"] for x in st.list(include_superseded=True)]
    s.check("K5 supersede: only the new entry is current",
            new["id"] in cur and pa["id"] not in cur, str(cur))
    s.check("K5 supersede: both old and new are visible with include_superseded=True",
            new["id"] in every and pa["id"] in every, str(every))
    s.check("K5 supersede records supersedes=<old id> and the reason, inherits type+scope",
            new["supersedes"] == pa["id"] and new["reason"].startswith("owner asked")
            and new["type"] == "preference" and new["scope"] == "global", str(new))
    s.check("J2 store never rewrites a line: entries.jsonl before supersede is a byte-prefix after",
            after.startswith(before) and len(after) > len(before)
            and after[len(before):].count(b"\n") == 1, f"{len(before)} -> {len(after)}")
    s.check("K5 history() walks the supersede chain oldest -> newest",
            [x["id"] for x in st.history(new["id"])] == [pa["id"], new["id"]], "")
    tampered = bytearray(after)
    tampered[5:6] = b"X" if tampered[5:6] != b"X" else b"Y"
    s.check("negative: a seeded in-place edit of an earlier line fails the byte-prefix check",
            not bytes(tampered).startswith(before), "")
    try:
        st.supersede(pa["id"], "x", reason="")
        no_reason = False
    except ValueError:
        no_reason = True
    s.check("K5 a supersede without a reason is refused (never a silent replace)", no_reason, "")

    # ---- K5: conflicts
    c1 = st.add("preference", "Always use a dark background for videos", scope="global")
    c2 = st.add("preference", "Never use a dark background; use light paper", scope="global",
                subject="background")
    c1b = st.add("preference", "background: dark slate", scope=a_scope)
    pairs = st.conflicts()
    ids = {frozenset((a["id"], b["id"])) for a, b in pairs}
    s.check("K5 conflicts() surfaces two contradicting un-superseded preferences",
            frozenset((c1["id"], c2["id"])) in ids, str([(a["text"], b["text"]) for a, b in pairs]))
    s.check("K5 conflicts(): a global and a project-scoped entry on the same subject also pair",
            frozenset((c2["id"], c1b["id"])) in ids, "")
    s.check("K5 conflicts(): a superseded preference no longer conflicts",
            all(pa["id"] not in p for p in ids), "")
    s.check("K5 conflicts(): returns (older, newer) pairs",
            all(isinstance(p, tuple) and len(p) == 2 for p in pairs)
            and any(a["id"] == c1["id"] and b["id"] == c2["id"] for a, b in pairs), "")
    det = st.conflicts(detail=True)
    s.check("K5 conflicts(detail=True) names the subject and flags opposite polarity",
            any(d["subject"] == "background" and "opposite" in d["why"] for d in det), str(det)[:300])
    st.add("preference", "Prefer captions off by default", scope=b_scope)
    st.add("preference", "Prefer captions on by default", scope=store.project_scope("project-c"))
    s.check("K5 conflicts(): different projects' entries never pair (scopes do not overlap)",
            not any({"project:project-b", "project:project-c"} == {a["scope"], b["scope"]}
                    for a, b in st.conflicts()), "")
    dup1 = st.add("decision", "Spine: problem first", scope="global")
    dup2 = st.add("decision", "spine:  Problem first!", scope="global")
    s.check("K5 conflicts(): an identical restatement is a duplicate, not a conflict",
            frozenset((dup1["id"], dup2["id"])) not in
            {frozenset((a["id"], b["id"])) for a, b in st.conflicts()}, "")
    live = st.conflicts_with("Use a light background throughout", subject="background")
    s.check("K5 conflicts_with(): a live-brief statement surfaces the memory entries it contradicts",
            {c1["id"], c2["id"], c1b["id"]} <= {x["id"] for x in live}, str([x["id"] for x in live]))
    s.check("K5 subject_key heuristic: directive words stripped, head noun kept",
            store.subject_key({"text": "Use a female narration voice"}) == "voice"
            and store.subject_key({"text": "Never use a female voice"}) == "voice"
            and store.subject_key({"text": "Palette: teal"}) == "palette", "")

    # ---- K3: stale
    src = tmp / "repo" / "notes.md"
    src.parent.mkdir()
    src.write_text("the cache holds verdicts\n", encoding="utf-8")
    f1 = st.add("fact", "Cache holds classifier verdicts", source_path=str(src))
    s.check("K3 add(source_path=...) records the file's sha256 when no hash is given",
            f1["source_hash"] == hashlib.sha256(src.read_bytes()).hexdigest(), str(f1))
    s.check("K3 stale() is empty while the source is unchanged",
            not [x for x in st.stale() if x["id"] == f1["id"]], "")
    src.write_text("the cache was removed\n", encoding="utf-8")
    stl = st.stale()
    s.check("K3 stale() flags an entry after its source file is edited",
            any(x["id"] == f1["id"] and x["stale_reason"] == "source changed" for x in stl), str(stl))
    s.check("K3 stale() accepts a dict lookup and a callable lookup",
            [x["id"] for x in st.stale({str(src): f1["source_hash"]})] == []
            and any(x["id"] == f1["id"] for x in st.stale(lambda p: "0" * 64)), "")
    src.unlink()
    s.check("K3 stale() flags an entry whose source file is gone",
            any(x["id"] == f1["id"] and x["stale_reason"] == "source missing" for x in st.stale()), "")

    # ---- choices + inventions
    for i in range(5):
        st.append_choice({"project": "p", "build": f"b{i}", "palette_family": f"fam{i}",
                          "kit": "k", "opening_scene": f"open{i}", "spine": "s", "voice": "v"})
    rc = st.recent_choices(3)
    s.check("H3-data choices round-trip newest first (recent_choices(3))",
            [r["build"] for r in rc] == ["b4", "b3", "b2"] and all("ts" in r for r in rc), str(rc))
    ch = st.append_choice({"project": "q", "extra": 1})
    s.check("H3-data a choice line always carries every documented key (missing -> null) + extras",
            all(k in ch for k in store.CHOICE_KEYS) and ch["kit"] is None and ch["extra"] == 1, str(ch))
    inv1 = st.add_invention("palette", "ember-dusk", {"bg": "#201a17"}, "build-1", {"contrast": "PASS"})
    st.add_invention("diagram", "tide-chart", {"form": "x"}, "build-1", {"budget": True})
    st.add_invention("palette", "ember-dusk", {"bg": "#201a17"}, "build-2", {"contrast": "PASS"})
    s.check("H5-data inventions round-trip and filter by kind",
            len(st.inventions()) == 3 and [r["name"] for r in st.inventions("diagram")] == ["tide-chart"]
            and st.inventions("palette")[0]["id"] == inv1["id"], "")
    s.check("H5-data promotion_candidates(): an invention passing in 2 builds is offered",
            [c["name"] for c in st.promotion_candidates()] == ["ember-dusk"], str(st.promotion_candidates()))

    # ---- corrupt lines
    ep = H / "memory/entries.jsonl"
    with open(ep, "ab") as f:
        f.write(b"{this is not json\n[1,2,3]\n")
    st2 = Store()
    n_before = len(st2.all_entries())
    s.check("store: a corrupt JSONL line is skipped and reported, never fatal",
            n_before > 0 and len(st2.corrupt) == 2 and st2.corrupt[0]["line"] > 0, str(st2.corrupt))
    with open(ep, "ab") as f:
        f.write(b'{"torn": "tail with no newline"')
    torn = st2.add("fact", "written after a torn tail")
    s.check("store: an append after a torn last line starts on its own line (no merge)",
            any(x["id"] == torn["id"] for x in Store().all_entries()), "")

    # ---- index
    idx = st.write_index()
    txt = idx.read_text(encoding="utf-8")
    s.check("K1 index.md is written under memory/, mentions the live-brief precedence",
            idx == H / "memory/index.md" and "live brief outranks" in txt.lower(), txt[:200])
    s.check("K1 index.md shows current entries and hides superseded ones",
            new["id"] in txt and pa["id"] not in txt, "")
    s.check("K5 index.md surfaces unresolved conflicts at session start",
            "conflict" in txt.lower(), "")

with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)
    paths, store, migrate = fresh(env)
    st = store.Store()
    first = []
    for i in range(500):
        e = st.add(["preference", "decision", "lesson"][i % 3],
                   f"Entry number {i:03d}: " + "a fairly long statement about one design rule " * 3,
                   scope="global" if i % 2 else store.project_scope("big project"))
        first.append(e)
    p = st.write_index(max_bytes=4096)
    b = p.read_bytes()
    t = b.decode("utf-8")
    s.check("K1 index.md <= 4096 bytes even with 500 entries", len(b) <= 4096, str(len(b)))
    s.check("K1 index.md shows the NEWEST current entries and says how many are omitted",
            first[-1]["id"] in t and first[-2]["id"] in t and first[0]["id"] not in t
            and "older entries not shown" in t, t[-300:])
    small = st.render_index(max_bytes=300)
    s.check("K1 render_index honours an arbitrary small hard cap",
            len(small.encode("utf-8")) <= 300, str(len(small.encode())))

    # ---- CLI
    r = run("scripts/memory/store.py", ["add", "preference", "Prefer short scenes", "--json"], env=env)
    added = json.loads(r.stdout) if r.returncode == 0 else {}
    r2 = run("scripts/memory/store.py", ["list", "--json", "--type", "preference"], env=env)
    r3 = run("scripts/memory/store.py", ["supersede", added.get("id", "x"), "Prefer long scenes",
                                         "--reason", "changed mind", "--json"], env=env)
    r4 = run("scripts/memory/store.py", ["conflicts", "--json"], env=env)
    r5 = run("scripts/memory/store.py", ["index", "--json"], env=env)
    r6 = run("scripts/memory/store.py", ["export"], env=env)
    try:
        exported = json.loads(r6.stdout)
    except ValueError:
        exported = {}
    s.check("CLI store.py add|list|supersede|conflicts|index|export all run with --json in the sandbox",
            all(x.returncode == 0 for x in (r, r2, r3, r4, r5, r6))
            and added.get("source") == "cli"
            and any(x["id"] == added.get("id") for x in json.loads(r2.stdout))
            and json.loads(r3.stdout)["supersedes"] == added.get("id")
            and json.loads(r5.stdout)["bytes"] <= 4096
            and len(exported.get("entries", [])) == 502,
            " | ".join((x.stderr or "")[-200:] for x in (r, r2, r3, r4, r5, r6)))
    r7 = run("scripts/memory/store.py", ["supersede", "m-nope", "x", "--reason", "y"], env=env)
    s.check("CLI supersede of an unknown id fails with a clear error (exit 2), writes nothing",
            r7.returncode == 2 and "no entry" in r7.stderr, r7.stderr)

# ------------------------------------------------------------------- J6 migrate
V1_PREFS = ["Keep videos under four minutes", "Use British spelling", "Prefer hand-drawn diagrams"]
with tmpdir() as td:
    tmp = Path(td)
    env = sandbox_env(tmp)
    paths, store, migrate = fresh(env)
    H = Path(env["COORDN8_HOME"])
    # schema-1 fixture: flat preferences list + entries lacking scope/confidence + a corrupt line
    (H / "memory").mkdir(parents=True)
    (H / "voices/local-engine").mkdir(parents=True)
    (H / "schema.json").write_bytes(b'{"schema": 1, "created": "2026-01-01T00:00:00", "migrated": []}\n')
    (H / "memory/preferences.json").write_bytes(json.dumps(V1_PREFS, indent=2).encode() + b"\n")
    (H / "memory/entries.jsonl").write_bytes(
        b'{"id": "m-0000000000a1", "type": "lesson", "text": "Ship captions", "date": "2026-01-02"}\n'
        b'not json at all\n'
        b'{"id": "m-0000000000a2", "type": "decision", "text": "Spine: problem first", '
        b'"date": "2026-01-03", "scope": "global", "confidence": "high", "source": "chat", '
        b'"source_quote": null, "supersedes": null, "source_hash": null, "source_path": null}\n')
    (H / "profiles").mkdir()
    (H / "profiles/acme.json").write_bytes(b'{"brand": "acme"}\n')
    (H / "voices/local-engine/model.onnx").write_bytes(b"\0" * 2048)
    (H / "voices/local-engine/voices.json").write_bytes(b'{"af": 1}\n')
    v1 = tree(H)

    stt = migrate.status()
    s.check("J6 status() on a v1 fixture reports schema 1 and a pending 1->2 migration",
            stt["schema_version"] == 1 and stt["pending"] and stt["pending"][0]["to"] == 2
            and not stt["error"], json.dumps(stt))
    s.check("J6 status() is read-only (tree unchanged)", tree(H) == v1, "")

    res = migrate.run()
    bdir = Path(res.get("backup") or "/nonexistent")
    s.check("J6 run(): migrates and names its backup backups/<ts>-pre-migrate-v1-to-v2",
            res["ok"] and res["changed"] and bdir.is_dir()
            and bdir.name.endswith("-pre-migrate-v1-to-v2") and bdir.parent == H / "backups", str(res))
    man = json.loads((bdir / "manifest.json").read_text())
    s.check("J6 backup holds every user file byte-identically, excluding backups/ and voice model binaries",
            set(man["files"]) == {k for k in v1 if not k.endswith("/")} - {"voices/local-engine/model.onnx"}
            and all(hashlib.sha256((bdir / "data" / k).read_bytes()).hexdigest() == v1[k]
                    for k in man["files"]), str(sorted(man["files"])))
    sch = json.loads((H / "schema.json").read_text())
    s.check("J6 after migrate schema.json says 2 and records the step in 'migrated'",
            sch["schema"] == 2 and sch["created"] == "2026-01-01T00:00:00"
            and sch["migrated"][-1]["from"] == 1 and sch["migrated"][-1]["to"] == 2
            and sch["migrated"][-1]["imported"] == 3, json.dumps(sch))
    st = store.Store()
    prefs = [e for e in st.list(type="preference") if e.get("source") == "migrate"]
    s.check("J6 every v1 preference string became a typed entry (preference/global/med/migrate, quote kept)",
            sorted(e["source_quote"] for e in prefs) == sorted(V1_PREFS)
            and all(e["scope"] == "global" and e["confidence"] == "med" and e["type"] == "preference"
                    for e in prefs), json.dumps(prefs)[:300])
    a1 = st.get("m-0000000000a1")
    s.check("J6 existing entries gain missing fields (scope global, confidence med) and list what was added",
            a1 and a1["scope"] == "global" and a1["confidence"] == "med"
            and "scope" in a1["migrated_fields"] and "confidence" in a1["migrated_fields"], str(a1))
    raw = (H / "memory/entries.jsonl").read_bytes()
    s.check("J6 migrate keeps a complete line byte-exact and a corrupt line verbatim (never drops bytes)",
            b'"source_path": null}\n' in raw and b"\nnot json at all\n" in raw, "")
    s.check("J6 migrate leaves the legacy preferences.json in place (user data is never deleted)",
            (H / "memory/preferences.json").read_bytes() == (bdir / "data/memory/preferences.json").read_bytes(), "")
    s.check("J6 status() after migrate: nothing pending", not migrate.status()["pending"], "")

    v2 = tree(H, skip_backups=False)
    res2 = migrate.run()
    s.check("J6 migrate twice = no change (no write, no new backup)",
            res2["ok"] and not res2["changed"] and tree(H, skip_backups=False) == v2, str(res2))

    r = run("scripts/memory/migrate.py", ["status", "--json"], env=env)
    s.check("CLI migrate.py status --json runs in the sandbox", r.returncode == 0
            and json.loads(r.stdout)["schema_version"] == 2, r.stderr[-200:])
    r = run("scripts/memory/migrate.py", ["restore", bdir.name, "--json"], env=env)
    rr = json.loads(r.stdout) if r.stdout.strip() else {}
    s.check("J6 restore <backup> returns the exact v1 bytes (every file, nothing extra)",
            r.returncode == 0 and rr.get("ok") and tree(H) == v1, json.dumps(rr)[:300] + r.stderr[-200:])
    s.check("J6 restore takes a safety backup of the pre-restore state first",
            rr.get("safety_backup") and Path(rr["safety_backup"]).name.endswith("-pre-restore")
            and (Path(rr["safety_backup"]) / "data/schema.json").exists(), str(rr.get("safety_backup")))
    s.check("J6 restore never touches voice model binaries",
            (H / "voices/local-engine/model.onnx").read_bytes() == b"\0" * 2048, "")
    victim = sorted(man["files"])[0]
    good = (bdir / "data" / victim).read_bytes()
    (bdir / "data" / victim).write_bytes(good + b"tamper")
    pre = tree(H)
    rbad = migrate.restore(bdir)
    s.check("negative: restore from a seeded damaged backup is refused and changes nothing",
            not rbad["ok"] and "damaged" in rbad["error"] and tree(H) == pre, str(rbad))
    (bdir / "data" / victim).write_bytes(good)
    fake = H / "backups" / "not-a-backup"
    fake.mkdir()
    s.check("J6 restore refuses a directory that is not a backup (and changes nothing)",
            not migrate.restore(fake)["ok"] and tree(H) == v1, "")

    # shape detection: a home stamped 2 with an un-imported legacy file is still pending
    (H / "schema.json").write_bytes(b'{"schema": 2, "created": "x", "migrated": []}\n')
    s.check("J6 a schema-2 stamp with un-imported legacy preferences is still reported pending",
            bool(migrate.status()["pending"]), "")
    (H / "schema.json").write_bytes(b'{"schema": 9}\n')
    s9 = migrate.status()
    r9 = migrate.run()
    s.check("J6 a NEWER schema than the package understands is refused, not migrated",
            s9["error"] and "newer" in s9["error"] and not r9["ok"]
            and json.loads((H / "schema.json").read_text())["schema"] == 9, str(s9))

with tmpdir() as td:  # no schema.json at all + a legacy file -> inferred v1, still migrates
    env = sandbox_env(Path(td))
    paths, store, migrate = fresh(env)
    H = Path(env["COORDN8_HOME"])
    (H / "memory").mkdir(parents=True)
    (H / "memory/preferences.json").write_text('["Prefer serif titles"]')
    st0 = migrate.status()
    r0 = migrate.run()
    s.check("J6 a home with no schema.json but a legacy preferences.json is inferred v1 and migrated",
            st0["schema_version"] == 1 and st0["schema_source"] == "inferred" and r0["changed"]
            and json.loads((H / "schema.json").read_text())["schema"] == 2
            and [e["text"] for e in store.Store().list()] == ["Prefer serif titles"], str(r0))

with tmpdir() as td:  # concurrent appenders: every line survives intact
    env = sandbox_env(Path(td))
    paths, store, migrate = fresh(env)
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from memory.store import Store; st = Store()\n"
            "for i in range(50): st.add('fact', 'worker %%s line %%d ' %% (sys.argv[1], i) + 'x' * 300)"
            % str(ROOT / "scripts"))
    e2 = dict(os.environ); e2.update(env)
    procs = [subprocess.Popen([sys.executable, "-c", code, str(w)], env=e2) for w in range(4)]
    rcs = [p.wait(timeout=120) for p in procs]
    stc = store.Store()
    rows = stc.all_entries()
    s.check("store: 4 concurrent writers x 50 appends -> 200 intact lines, zero corrupt",
            rcs == [0, 0, 0, 0] and len(rows) == 200 and not stc.corrupt
            and len({r["id"] for r in rows}) == 200, f"{rcs} {len(rows)} {stc.corrupt[:2]}")

with tmpdir() as td:  # fresh home: nothing to migrate, install-style ensure_user_home writes v2
    env = sandbox_env(Path(td))
    paths, store, migrate = fresh(env)
    paths.ensure_user_home()
    sch = json.loads((paths.user_home() / "schema.json").read_text())
    s.check("J6 a fresh install stamps schema 2 and status() reports nothing pending",
            sch["schema"] == 2 and not migrate.status()["pending"] and not migrate.status()["error"], str(sch))

# ------------------------------------------------------------------- doctor probe
with tmpdir() as td:
    env = sandbox_env(Path(td))
    paths, store, migrate = fresh(env)
    spec = importlib.util.spec_from_file_location("dc_userdata", ROOT / "scripts/doctor_checks/userdata.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    H = Path(env["COORDN8_HOME"])
    empty = mod.probe()
    s.check("doctor probe on a not-yet-created home returns a dict and creates nothing",
            isinstance(empty, dict) and "summary" in empty and isinstance(empty["lines"], list)
            and not any(H.iterdir()), str(empty))
    (H / "memory").mkdir()
    (H / "memory/preferences.json").write_text('["v1 pref"]')
    st = store.Store()
    st.add("preference", "Prefer teal accents")
    st.write_index()
    before = tree(H, skip_backups=False)
    res = mod.probe()
    txt = " ".join(res["lines"])
    s.check("doctor probe returns {summary, lines} without writing anything",
            isinstance(res, dict) and isinstance(res["summary"], str) and res["lines"]
            and tree(H, skip_backups=False) == before, str(res)[:300])
    s.check("doctor probe reports zone path, schema version, pending migration, counts, index size, backups",
            str(H) in txt and "schema:" in txt and "migration pending" in txt
            and "memory entries: 1 current" in txt and "memory index:" in txt and "backups: 0" in txt
            and "MIGRATION PENDING" in res["summary"], txt)
    (H / "schema.json").write_text("{broken")
    res = mod.probe()
    s.check("doctor probe never raises on a broken schema.json (reports it)",
            isinstance(res, dict) and "schema problem" in " ".join(res["lines"]), str(res)[:300])
    r = run("scripts/doctor.py", ["--json"], env=env, timeout=120)
    try:
        dj = json.loads(r.stdout)
        found = json.dumps(dj).count("userdata") > 0
    except ValueError:
        found = "user data" in r.stdout
    s.check("doctor.py picks up the userdata extension section",
            found, (r.stdout[-300:] + r.stderr[-300:]))

real_after = tree(real_home_store) if real_home_store.exists() else None
s.check("hermetic: the real ~/.coordn8 was not touched by this suite", real_before == real_after, "")

sys.exit(s.finish())
