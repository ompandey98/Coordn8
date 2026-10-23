#!/usr/bin/env python3
"""
Coordn8 — AUTOMATIC FRAME REVIEW (Stage V, requirement E6; variant ranking, H4).

Runs after every render, with no human step. The pipeline:

  prepare <build>       contact sheets (one per scene) at exact beat-map times + a BLIND
                        brief (sheets + exact sentences only) + an answer template, all
                        under <build>/_build/review/. Freshness is asserted first (the mp4
                        must be newer than every scene source); frames are decoded into
                        memory and never written; the run fails if any frame file is left.
  (the coordn8-frame-reviewer agent reads the sheets with Read and returns verdicts JSON;
   the calling agent saves that reply verbatim as _build/review/verdicts.json)
  collect <verdicts>    every spoken line must have exactly one verdict for its exact
                        sentence; sheets must still be fresh and unmodified; the verdicts
                        are normalised into _build/stage_v/verdicts.json and validated by
                        `stage_v.py check`. `contradicts` anywhere -> BLOCK (exit 1).
                        Result: _build/review/stage_v.json.
  status <build>        PASS / BLOCK / pending — a review that has not run is an OPEN
                        ITEM, never a pass.
  clean <build>         delete the sheets (verdicts and manifest are kept).
  rank-prepare <dir>    H4: anonymise variant sheets into a blind ranking brief.
  rank <dir>            H4: map the reviewer's ranking back to variants -> review/ranked.json.

Library API used by other parts of the pipeline:
    prepare(build, prev=None, ...) -> dict
    collect(verdicts_path, build=None) -> dict
    status(build) -> dict
    prepare_variants(variants_dir) -> dict
    rank(variants_dir) -> list[dict]   # [] while the blind reviewer has not ranked yet

Exit codes: 0 PASS, 1 REVIEW/BLOCK/FAIL/STALE, 2 SKIPPED/pending, 3 ERROR.
This module calls no model and no network service. The reviewer is a Claude subagent
that reads PNG/JPEG with its Read tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "render"))
sys.path.insert(0, str(HERE))

import contact_sheet as cs  # noqa: E402
import blind_brief as bb  # noqa: E402

try:
    from diskguard import Budget, DiskBudgetError, reclaim  # noqa: E402
except Exception:  # noqa: BLE001
    Budget = None  # type: ignore
    DiskBudgetError = RuntimeError  # type: ignore
    reclaim = None  # type: ignore

try:
    from parallel import compute_workers  # noqa: E402
except Exception:  # noqa: BLE001
    def compute_workers(kind: str = "cpu", per_worker_mb: int = 600) -> int:  # type: ignore
        return 2

STAGE_V = SCRIPTS / "gates" / "stage_v.py"
EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, EXIT_ERROR = 0, 1, 2, 3
MANIFEST = "manifest.json"
NOT_VERIFIED = [
    "Gates measure; a PASS here means a blind reviewer found no contradiction on the sampled "
    "frames — it is not proof the whole video is good.",
    "Only the sampled frames were reviewed (85-90% through each line, 4 frames before each cut, "
    "-16/-3/+2 around each hit); motion between samples was not seen.",
    "Audio quality, pacing and loudness are not judged by this review.",
]


# --------------------------------------------------------------------------- locating
def _first(paths):
    for p in paths:
        if p and Path(p).is_file():
            return Path(p)
    return None


def locate(build: Path) -> dict:
    """Find the build's video, beat map and optional plan files. Accepts a Coordn8 build
    folder (<slug>.mp4 + _build/...) or a flat folder (mp4 + beatmap.json side by side)."""
    build = Path(build)
    b = build / "_build"
    mp4s = sorted(p for p in build.glob("*.mp4") if p.is_file())
    if not mp4s and b.is_dir():
        mp4s = sorted(p for p in b.glob("*.mp4") if p.is_file())
    return {
        "build": build,
        "mp4": mp4s[0] if mp4s else None,
        "beatmap": _first([b / "beatmap.json", build / "beatmap.json"]),
        "storyboard": _first([b / "storyboard.json", build / "storyboard.json"]),
        "hits": _first([b / "hits.json", build / "hits.json"]),
        "words": _first([b / "words.json", build / "words.json"]),
    }


def scene_sources(build: Path, extra=()) -> list[Path]:
    """Every source whose edit must predate the render: scene code, beat map, storyboard."""
    build = Path(build)
    b = build / "_build"
    srcs = [p for p in list(build.glob("video/*.js")) + list(build.glob("video/*.py"))
            + (list((b / "scenes").glob("*.js")) if (b / "scenes").is_dir() else [])
            if p.is_file() and p.name != "scene_runtime.js"]
    for rel in ("beatmap.json", "storyboard.json", "words.json", "hits.json"):
        if (b / rel).is_file():
            srcs.append(b / rel)
    srcs += [Path(p) for p in extra if Path(p).is_file()]
    seen, out = set(), []
    for p in srcs:
        r = str(Path(p).resolve())
        if r not in seen:
            seen.add(r)
            out.append(Path(p))
    return out


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8")) if p and Path(p).is_file() else None


def _emit(res: dict) -> None:
    print(json.dumps(res, ensure_ascii=False))


def _newest(paths) -> tuple[float, str]:
    m, name = 0.0, ""
    for p in paths:
        p = Path(p)
        if p.exists() and p.stat().st_mtime > m:
            m, name = p.stat().st_mtime, str(p)
    return m, name


# --------------------------------------------------------------------------- prepare
def prepare(build, prev=None, *, sources=(), out=None, cell_width: int = 0,
            fmt: str = "jpg", mp4=None) -> dict:
    t0 = time.monotonic()
    build = Path(build)
    loc = locate(build)
    if mp4:
        loc["mp4"] = Path(mp4)
    review = Path(out) if out else build / "_build" / "review"
    res: dict = {"gate": "frame_review", "step": "prepare", "build": str(build),
                 "review_dir": str(review)}
    if not cs.have_ffmpeg():
        return dict(res, status="SKIPPED", exit=EXIT_SKIPPED,
                    summary="ffmpeg/ffprobe not installed — frame review cannot run; Stage V "
                            "stays an OPEN ITEM (install ffmpeg, then re-run).")
    if not cs.HAVE_PIL:
        return dict(res, status="SKIPPED", exit=EXIT_SKIPPED,
                    summary="Pillow not installed — contact sheets cannot be composed; Stage V "
                            "stays an OPEN ITEM (pip install Pillow, then re-run).")
    if not loc["mp4"] or not loc["beatmap"]:
        return dict(res, status="SKIPPED", exit=EXIT_SKIPPED,
                    summary=f"no rendered video or beat map in {build} — the render has not run; "
                            f"Stage V is an OPEN ITEM, never a pass.")

    srcs = scene_sources(build, sources)
    newest, newest_name = _newest(srcs)
    mp4_m = loc["mp4"].stat().st_mtime
    if srcs and mp4_m < newest:
        return dict(res, status="STALE", exit=EXIT_REVIEW,
                    summary=f"REFUSED: the video {loc['mp4'].name} is older than the source edit "
                            f"{Path(newest_name).name} — re-render before reviewing. Reviewing a "
                            f"stale artifact reports already-fixed defects and discredits the review.")

    beatmap = _load(loc["beatmap"]) or {}
    storyboard = _load(loc["storyboard"])
    hits = _load(loc["hits"])
    if isinstance(hits, dict):
        hits = hits.get("hits")
    info = cs.probe(loc["mp4"])
    fps = float(beatmap.get("fps") or 0) or info["fps"]
    if info["fps"] and abs(fps - info["fps"]) > 0.01:
        return dict(res, status="ERROR", exit=EXIT_ERROR,
                    summary=f"beat map says {fps} fps but the video is {info['fps']:.3f} fps — "
                            f"frame times would be wrong; fix the fps mismatch first")
    pl = cs.plan(beatmap, fps=info["fps"], frames=info["frames"], duration=info["duration"],
                 storyboard=storyboard, hits=hits, words=_load(loc["words"]))
    if pl["errors"]:
        return dict(res, status="ERROR", exit=EXIT_ERROR, errors=pl["errors"],
                    summary="; ".join(pl["errors"])[:400])
    nlines = sum(len(s["lines"]) for s in pl["scenes"])
    if not nlines:
        return dict(res, status="SKIPPED", exit=EXIT_SKIPPED,
                    summary="the beat map has no spoken lines — nothing to review")

    prev_dir = Path(prev) if prev else None
    if prev_dir is None:
        auto = build / "_build" / "prev"
        if locate(auto)["mp4"] and locate(auto)["beatmap"]:
            prev_dir = auto
    prev_mp4, pinfo = None, None
    if prev_dir is not None:
        ploc = locate(prev_dir)
        if ploc["mp4"] and ploc["beatmap"]:
            prev_mp4 = ploc["mp4"]
            pinfo = cs.probe(prev_mp4)
            cs.pair_previous(pl, _load(ploc["beatmap"]), pinfo["fps"], pinfo["frames"])
        else:
            res["prev_warning"] = f"--prev {prev_dir} has no video + beat map; no pairing"

    # Disk: snapshot images first, so a frame left behind by THIS run is caught.
    root_scan = build
    before = cs.image_files(root_scan)
    review.mkdir(parents=True, exist_ok=True)
    for old in review.glob("sheet_*"):          # a re-run replaces, never accumulates
        old.unlink()

    def budget_check(frame_bytes: int, n: int) -> None:
        if Budget is None:
            return
        Budget(out_dir=review, frame_count=n, measured_frame_bytes=frame_bytes,
               cap_bytes=int(2 * 1024 ** 3)).check()

    cw = cell_width or cs.default_cell_width(info["width"])
    try:
        sheets = cs.compose(pl, loc["mp4"], review, cell_width=cw, src_w=info["width"],
                            src_h=info["height"], prev_mp4=prev_mp4,
                            prev_fps=pinfo["fps"] if pinfo else None, fmt=fmt,
                            workers=max(1, min(8, compute_workers("cpu"))),
                            budget_check=budget_check)
    except DiskBudgetError as e:  # type: ignore[misc]
        return dict(res, status="ERROR", exit=EXIT_ERROR, summary=str(e)[:400])
    except RuntimeError as e:
        return dict(res, status="ERROR", exit=EXIT_ERROR, summary=str(e)[:400])

    sheet_paths = {str(Path(s["path"]).resolve()) for s in sheets}
    after = cs.image_files(root_scan)
    leftovers = sorted((after - before) - sheet_paths)

    lines = {}
    for sc in pl["scenes"]:
        for ln in sc["lines"]:
            lines[ln["cell"]] = {k: ln[k] for k in ("line_idx", "line_name", "sentence", "start",
                                                     "end", "frame", "t", "fraction")}
            lines[ln["cell"]]["scene"] = sc["id"]
            if "prev" in ln:
                lines[ln["cell"]]["prev"] = ln["prev"]
                lines[ln["cell"]]["prev_status"] = ln.get("prev_status")
    manifest = {
        "stage": "V", "reviewer": bb.REVIEWER_AGENT, "created": time.time(),
        "build": str(build), "mp4": str(loc["mp4"]), "mp4_sha256": cs.sha256(loc["mp4"]),
        "mp4_mtime": mp4_m, "beatmap": str(loc["beatmap"]),
        "fps": info["fps"], "frames": info["frames"], "duration": info["duration"],
        "source_size": [info["width"], info["height"]],
        "sources": [{"path": str(p), "mtime": p.stat().st_mtime} for p in srcs],
        "prev": ({"dir": str(prev_dir), "mp4": str(prev_mp4)} if prev_mp4 else None),
        "plan": pl, "lines": lines, "sheets": sheets,
        "frames_left_on_disk": len(leftovers),
    }
    brief = bb.write(manifest, review)
    claims = bb.claim_strings(build)
    leaks = bb.find_leaks(brief["text"], claims, [v["sentence"] for v in lines.values()])
    manifest["blind"] = {"claims_scanned": len(claims), "leaks": leaks,
                         "brief": brief["md"], "brief_json": brief["json"],
                         "template": brief["template"]}
    manifest["template_path"] = brief["template"]
    (review / MANIFEST).write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n",
                                   encoding="utf-8")

    res.update({"sheets": [s["path"] for s in sheets], "slots": len(lines),
                "brief": brief["md"], "template": brief["template"],
                "manifest": str(review / MANIFEST), "frames_left_on_disk": len(leftovers),
                "previous_cut": bool(prev_mp4), "seconds": round(time.monotonic() - t0, 2)})
    if leftovers:
        for p in leftovers:                      # never leave a frame behind
            Path(p).unlink(missing_ok=True)
        return dict(res, status="ERROR", exit=EXIT_ERROR, leftovers=leftovers,
                    summary=f"{len(leftovers)} frame file(s) were left on disk by the review "
                            f"(removed now) — frames must stream through memory only")
    if leaks:
        for p in (brief["md"], brief["json"], brief["template"]):
            Path(p).unlink(missing_ok=True)
        return dict(res, status="ERROR", exit=EXIT_ERROR, leaks=leaks[:10],
                    summary=f"REFUSED: the blind brief would leak {len(leaks)} builder claim(s); "
                            f"brief deleted — a reviewer shown claims verifies the claims")
    return dict(res, status="PASS", exit=EXIT_PASS,
                summary=f"{len(sheets)} sheet(s), {len(lines)} verdict slot(s); dispatch "
                        f"{bb.REVIEWER_AGENT} with ONLY {brief['md']} and the sheets"
                        + ("" if prev_mp4 else " (no previous cut — 'what used to be here?' "
                                               "cannot be asked)"))


# --------------------------------------------------------------------------- collect
def _parse_verdicts(path: Path):
    txt = path.read_text(encoding="utf-8")
    m = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", txt, re.S)
    raw = m.group(1) if m else txt
    data = json.loads(raw)
    if isinstance(data, list):
        data = {"verdicts": data}
    return data


def _find_manifest(verdicts_path: Path, build) -> Path | None:
    if build:
        p = Path(build) / "_build" / "review" / MANIFEST
        return p if p.is_file() else None
    for d in [verdicts_path.parent] + list(verdicts_path.parents)[:3]:
        for cand in (d / MANIFEST, d / "_build" / "review" / MANIFEST, d / "review" / MANIFEST):
            if cand.is_file():
                return cand
    return None


def freshness(manifest: dict) -> list[str]:
    """Sheets must exist, be unmodified (hash) and be newer than every source + the mp4."""
    probs = []
    srcs = [Path(s["path"]) for s in manifest.get("sources", [])] + [Path(manifest["mp4"])]
    newest, newest_name = _newest(srcs)
    for sh in manifest.get("sheets", []):
        p = Path(sh["path"])
        if not p.is_file():
            probs.append(f"sheet missing: {p.name} — re-run prepare")
            continue
        if cs.sha256(p) != sh["sha256"]:
            probs.append(f"sheet {p.name} was modified after prepare (hash mismatch) — refused")
        if p.stat().st_mtime < newest:
            probs.append(f"STALE: sheet {p.name} is older than {Path(newest_name).name} — the "
                         f"source changed after the sheets were made; re-render and re-prepare")
    mp4 = Path(manifest["mp4"])
    if mp4.is_file() and cs.sha256(mp4) != manifest.get("mp4_sha256"):
        probs.append(f"STALE: {mp4.name} changed since the sheets were made — re-prepare")
    return probs


def collect(verdicts_path, build=None) -> dict:
    vp = Path(verdicts_path)
    res: dict = {"gate": "frame_review", "step": "collect", "verdicts": str(vp)}
    if not vp.is_file():
        return dict(res, status="SKIPPED", exit=EXIT_SKIPPED,
                    summary=f"no verdicts at {vp} — the blind review has not run; Stage V is an "
                            f"OPEN ITEM, never a pass")
    mpath = _find_manifest(vp, build)
    if mpath is None:
        return dict(res, status="ERROR", exit=EXIT_ERROR,
                    summary="no review manifest found — run `frame_review.py prepare <build>` first")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    review = mpath.parent
    out_path = review / "stage_v.json"
    build_dir = Path(manifest["build"])

    def _finish(r: dict) -> dict:
        r["not_verified"] = NOT_VERIFIED
        out_path.write_text(json.dumps(r, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        r["result_file"] = str(out_path)
        return r

    stale = freshness(manifest)
    if stale:
        return _finish(dict(res, status="STALE", exit=EXIT_REVIEW, problems=stale,
                            summary="REFUSED: " + stale[0]))
    try:
        data = _parse_verdicts(vp)
    except (ValueError, json.JSONDecodeError) as e:
        return _finish(dict(res, status="ERROR", exit=EXIT_ERROR,
                            summary=f"could not parse verdicts: {e}"))

    slots = bb.slots_from_manifest(manifest)
    by_slot = {s["slot"]: s for s in slots}
    by_idx = {str(s["line_idx"]): s for s in slots}
    got: dict[str, dict] = {}
    problems = []
    if data.get("refused"):
        return _finish(dict(res, status="STALE", exit=EXIT_REVIEW,
                            problems=[str(data.get("refused"))] + list(data.get("freshness_problems") or []),
                            summary=f"the reviewer REFUSED: {data.get('refused')} — re-render and re-prepare"))
    by_frame = {s["frame"]: s for s in slots}
    sent_count: dict[str, int] = {}
    for s_ in slots:
        sent_count[bb._norm(s_["sentence"])] = sent_count.get(bb._norm(s_["sentence"]), 0) + 1
    by_sent = {bb._norm(s_["sentence"]): s_ for s_ in slots if sent_count[bb._norm(s_["sentence"])] == 1}
    if data.get("contaminated_brief"):
        problems.append(f"the reviewer reported a contaminated brief: {str(data['contaminated_brief'])[:200]}")
    for i, v in enumerate(data.get("verdicts") or []):
        key = v.get("slot")
        if key is None and v.get("line_idx") is not None:
            s = by_idx.get(str(v["line_idx"]))
            key = s["slot"] if s else None
        if key is None and v.get("frame") in by_frame:        # the agent's native format
            key = by_frame[v["frame"]]["slot"]
        if key is None and bb._norm(v.get("sentence", "")) in by_sent:
            key = by_sent[bb._norm(v.get("sentence", ""))]["slot"]
        if key not in by_slot:
            problems.append(f"verdict[{i}] names unknown slot {v.get('slot') or v.get('line_idx')!r}")
            continue
        if key in got:
            problems.append(f"slot {key}: answered twice — one verdict per spoken line")
            continue
        exp = by_slot[key]["sentence"]
        if bb._norm(v.get("sentence", "")) != bb._norm(exp):
            problems.append(f"slot {key}: verdict sentence {str(v.get('sentence'))[:60]!r} is not "
                            f"the exact spoken sentence {exp[:60]!r}")
        nv = dict(v)
        nv.setdefault("frame", by_slot[key]["frame"])
        nv["slot"] = key
        nv["line_idx"] = by_slot[key]["line_idx"]
        got[key] = nv
    missing = [s for s in by_slot if s not in got]
    for s in missing:
        problems.append(f"slot {s}: NO VERDICT — every spoken line must be reviewed")

    events_bad = []
    ev_cells = {e["cell"] for e in bb.events_from_manifest(manifest)}
    for e in data.get("events") or []:
        if e.get("cell") in ev_cells and e.get("ok") is False:
            events_bad.append(f"{e['cell']}: {str(e.get('note', ''))[:160]}")

    normalized = {"stage": "V", "reviewer": data.get("reviewer", bb.REVIEWER_AGENT),
                  "verdicts": [got[s] for s in by_slot if s in got]}
    sv_dir = build_dir / "_build" / "stage_v"
    sv_dir.mkdir(parents=True, exist_ok=True)
    sv_path = sv_dir / "verdicts.json"
    sv_path.write_text(json.dumps(normalized, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (review / "verdicts.normalized.json").write_text(
        json.dumps(normalized, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    sv_payload, sv_rc = None, None
    if normalized["verdicts"]:
        cp = subprocess.run([sys.executable, str(STAGE_V), "check", str(sv_path)],
                            capture_output=True, text=True, timeout=120)
        sv_rc = cp.returncode
        for line in cp.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    sv_payload = json.loads(line)
                except ValueError:
                    pass
    blocking = list((sv_payload or {}).get("blocking") or [])
    defects = list((sv_payload or {}).get("ranked_defects") or []) + events_bad
    incomplete = list((sv_payload or {}).get("incomplete") or [])
    if blocking:
        status = "BLOCK"
    elif problems:
        status = "FAIL"
    elif defects or incomplete or sv_rc not in (0,):
        status = "REVIEW"
    else:
        status = "PASS"
    summary = {
        "BLOCK": f"BLOCKED — {len(blocking)} frame(s) contradict their spoken sentence",
        "FAIL": f"{len(problems)} slot problem(s): " + (problems[0] if problems else ""),
        "REVIEW": f"{len(defects)} ranked defect(s), {len(incomplete)} incomplete verdict(s)",
        "PASS": f"all {len(slots)} spoken lines reviewed blind; no frame contradicts its sentence",
    }[status]
    return _finish(dict(res, status=status, exit=EXIT_PASS if status == "PASS" else EXIT_REVIEW,
                        slots_total=len(slots), slots_answered=len(got), missing_slots=missing,
                        slot_problems=problems, blocking=blocking, ranked_defects=defects,
                        incomplete=incomplete, stage_v_exit=sv_rc, stage_v=sv_payload,
                        stage_v_verdicts=str(sv_path), summary=summary))


# --------------------------------------------------------------------------- status/clean
def status(build) -> dict:
    review = Path(build) / "_build" / "review"
    res = {"gate": "frame_review", "step": "status", "build": str(build)}
    mpath = review / MANIFEST
    if not mpath.is_file():
        return dict(res, status="PENDING", exit=EXIT_SKIPPED,
                    summary="frame review not prepared — Stage V is an OPEN ITEM")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    stale = freshness(manifest) if any(Path(s["path"]).is_file() for s in manifest["sheets"]) else []
    out = review / "stage_v.json"
    if not out.is_file():
        return dict(res, status="PENDING", exit=EXIT_SKIPPED, stale=stale,
                    summary="sheets prepared, no verdicts collected yet — dispatch "
                            f"{bb.REVIEWER_AGENT}; Stage V is an OPEN ITEM")
    r = json.loads(out.read_text(encoding="utf-8"))
    if stale:
        return dict(res, status="STALE", exit=EXIT_REVIEW, stale=stale,
                    summary="sources changed after the review — re-prepare and re-review")
    st = r.get("status", "ERROR")
    return dict(res, status=st, exit=EXIT_PASS if st == "PASS" else EXIT_REVIEW,
                summary=r.get("summary", ""))


def clean(build) -> dict:
    review = Path(build) / "_build" / "review"
    sheets = sorted(review.glob("sheet_*")) + sorted(review.glob("candidate_*"))
    if reclaim is not None:
        r = reclaim(sheets)
        freed = r["freed"]
    else:
        for p in sheets:
            p.unlink(missing_ok=True)
        freed = f"{len(sheets)} file(s)"
    left = cs.image_files(review) if review.is_dir() else set()
    return {"gate": "frame_review", "step": "clean", "status": "PASS" if not left else "REVIEW",
            "exit": EXIT_PASS if not left else EXIT_REVIEW, "removed": len(sheets),
            "freed": freed, "images_left": len(left)}


# --------------------------------------------------------------------------- H4 ranking
def _variant_dirs(variants_dir: Path) -> list[Path]:
    return sorted(d for d in Path(variants_dir).iterdir()
                  if d.is_dir() and d.name not in ("review",) and not d.name.startswith("."))


def _variant_sheet(d: Path) -> Path | None:
    for pat in ("sheet*.jpg", "sheet*.png", "contact*.jpg", "contact*.png", "*.jpg", "*.png"):
        hits = sorted(d.rglob(pat))
        if hits:
            return hits[0]
    return None


def _labels(names: list[str], salt: str) -> dict:
    """Blind labels A, B, C… in an order unrelated to the builder's numbering."""
    order = sorted(names, key=lambda n: hashlib.sha256((salt + n).encode()).hexdigest())
    return {n: chr(ord("A") + i) for i, n in enumerate(order)}


def prepare_variants(variants_dir) -> dict:
    vd = Path(variants_dir)
    review = vd / "review"
    review.mkdir(parents=True, exist_ok=True)
    dirs = _variant_dirs(vd)
    found = {d.name: _variant_sheet(d) for d in dirs}
    missing = [n for n, p in found.items() if p is None]
    if len(dirs) < 2 or missing:
        return {"status": "ERROR", "exit": EXIT_ERROR,
                "summary": f"need >= 2 variants each with a contact sheet; missing: {missing}"}
    labels = _labels(sorted(found), vd.name)
    sentences = []
    for d in dirs:
        for cand in (d / "sentence.txt", d / "beatmap.json", vd / "sentence.txt"):
            if cand.is_file():
                if cand.suffix == ".json":
                    sentences += [cs.display_text(s.get("text", "")) for s in
                                  (json.loads(cand.read_text(encoding="utf-8")).get("sentences") or [])]
                else:
                    sentences += [ln.strip() for ln in cand.read_text(encoding="utf-8").splitlines()
                                  if ln.strip()]
                break
    sentences = list(dict.fromkeys(s for s in sentences if s))
    cands = []
    for name, sheet in sorted(found.items(), key=lambda kv: labels[kv[0]]):
        dst = review / f"candidate_{labels[name]}{sheet.suffix.lower()}"
        shutil.copyfile(sheet, dst)
        cands.append({"label": labels[name], "sheet": str(dst), "sha256": cs.sha256(dst)})
    L = [f"# Variant ranking — blind brief for `{bb.REVIEWER_AGENT}`", "", bb.LIMITS, "",
         "Each candidate is a different treatment of the SAME moment. You do not know which one "
         "the builder prefers, and the labels are in no meaningful order. Read only these sheets:", ""]
    L += [f"- candidate `{c['label']}`: `{c['sheet']}`" for c in cands]
    L += ["", "The exact sentence(s) spoken over this moment:", ""]
    L += [f"> {s}" for s in sentences] or ["> (no sentence supplied — judge clarity alone)"]
    L += ["", "Rank them best first. For each give the reason (what it shows better or worse, "
          "specifically) and `shows_what_is_said`: match | partial | contradicts.", "",
          "Reply with ONE fenced ```json block:",
          '`{"ranking": [{"label": "B", "shows_what_is_said": "match", "reason": "..."}, ...]}`', ""]
    brief = review / "rank_brief.md"
    brief.write_text("\n".join(L), encoding="utf-8")
    # The label map is for rank() only; the reviewer's brief never names it.
    (review / ".label_map.json").write_text(json.dumps({"labels": labels, "candidates": cands},
                                                       indent=1) + "\n", encoding="utf-8")
    return {"status": "PASS", "exit": EXIT_PASS, "brief": str(brief),
            "candidates": [c["sheet"] for c in cands],
            "ranking_file": str(review / "ranking.json")}


def rank(variants_dir, ranking=None) -> list[dict]:
    """Ranked variants, best first, from the BLIND reviewer's answer.

    Returns [] (and prepares the blind brief) while the reviewer has not ranked yet.
    Raises ValueError on a malformed ranking (a label missing/duplicated, no reason)."""
    vd = Path(variants_dir)
    review = vd / "review"
    lm = review / ".label_map.json"
    if not lm.is_file():
        prepare_variants(vd)
    rpath = Path(ranking) if ranking else review / "ranking.json"
    if not rpath.is_file():
        return []
    labels = json.loads(lm.read_text(encoding="utf-8"))["labels"]
    back = {v: k for k, v in labels.items()}
    data = _parse_verdicts(rpath)
    items = data.get("ranking") or data.get("verdicts") or []
    if items and all("rank" in it for it in items):
        items = sorted(items, key=lambda it: int(it["rank"]))
    seen = [str(it.get("label", "")).strip().upper() for it in items]
    if sorted(seen) != sorted(back):
        raise ValueError(f"ranking must name every candidate exactly once: got {seen}, "
                         f"expected {sorted(back)}")
    out = []
    for it in items:
        reason = str(it.get("reason", "")).strip()
        if len(reason) < 10:
            raise ValueError(f"candidate {it.get('label')}: the reviewer gave no reason")
        out.append({"variant": back[str(it["label"]).strip().upper()],
                    "label": str(it["label"]).strip().upper(), "reason": reason,
                    "shows_what_is_said": str(it.get("shows_what_is_said", "")).lower() or None,
                    "disqualified": str(it.get("shows_what_is_said", "")).lower() == "contradicts"})
    ok = [o for o in out if not o["disqualified"]]
    bad = [o for o in out if o["disqualified"]]
    ranked = ok + bad
    for i, o in enumerate(ranked, 1):
        o["rank"] = i
    (review / "ranked.json").write_text(json.dumps(
        {"reviewer": bb.REVIEWER_AGENT, "blind": True, "ranked": ranked,
         "best": ranked[0]["variant"] if ok else None,
         "alternates": [o["variant"] for o in ranked[1:]]}, indent=1) + "\n", encoding="utf-8")
    return ranked


# --------------------------------------------------------------------------- CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Coordn8 automatic frame review (Stage V).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("build")
    p.add_argument("--prev", help="previous cut's build folder (pairs 'what used to be here?')")
    p.add_argument("--mp4")
    p.add_argument("--sources", nargs="*", default=[])
    p.add_argument("--out")
    p.add_argument("--cell-width", type=int, default=0)
    p.add_argument("--format", choices=("jpg", "png"), default="jpg")
    c = sub.add_parser("collect")
    c.add_argument("verdicts")
    c.add_argument("--build")
    for name in ("status", "clean"):
        sub.add_parser(name).add_argument("build")
    rp = sub.add_parser("rank-prepare")
    rp.add_argument("variants_dir")
    rk = sub.add_parser("rank")
    rk.add_argument("variants_dir")
    rk.add_argument("--ranking")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "prepare":
            r = prepare(a.build, a.prev, sources=a.sources, out=a.out, cell_width=a.cell_width,
                        fmt=a.format, mp4=a.mp4)
        elif a.cmd == "collect":
            r = collect(a.verdicts, a.build)
        elif a.cmd == "status":
            r = status(a.build)
        elif a.cmd == "clean":
            r = clean(a.build)
        elif a.cmd == "rank-prepare":
            r = prepare_variants(a.variants_dir)
        else:
            ranked = rank(a.variants_dir, a.ranking)
            r = ({"status": "PASS", "exit": 0, "ranked": ranked} if ranked else
                 {"status": "PENDING", "exit": EXIT_SKIPPED,
                  "summary": "blind reviewer has not ranked yet — see review/rank_brief.md"})
    except ValueError as e:
        r = {"status": "FAIL", "exit": EXIT_REVIEW, "summary": str(e)}
    except Exception as e:  # noqa: BLE001
        r = {"status": "ERROR", "exit": EXIT_ERROR, "summary": f"{type(e).__name__}: {e}"}
    _emit(r)
    if r.get("summary"):
        print(f"[frame_review] {r['status']}: {r['summary']}", file=sys.stderr)
    return int(r.get("exit", EXIT_ERROR))


if __name__ == "__main__":
    sys.exit(main())
