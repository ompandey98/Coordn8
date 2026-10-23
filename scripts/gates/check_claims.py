#!/usr/bin/env python3
"""
Coordn8 gate — CLAIMS LEDGER (C3, both deliverables).

*If you cannot point to a line, you do not know it — read more, or mark it inferred.*

Every page ENDS with a claims ledger (claim -> path:line -> read | inferred), and every video ships
`<slug>.claims.md` beside `<slug>.mp4`, because a video cannot be scanned and the claims file is
how a reader checks it. This tool writes the video sidecar and proves the ledgers say exactly what
the claims manifest says.

    check_claims.py emit   claims.json --out Coordn8/<slug>.claims.md [--title T] [--repo R]
    check_claims.py verify page.html|<slug>.claims.md  claims.json [--repo R]
    check_claims.py video  Coordn8/<slug>.mp4 [--claims claims.json]

claims.json is the facts_trace shape (`{"claims": [{id, text, ref, status | inferred}]}`, the file
`build_page.py --claims-out` writes). A claim's basis is `read` when it carries a reference and is
not marked inferred, `inferred` when it is marked (`inferred: true`, or status/basis "inferred"),
and UNMARKED otherwise — an unmarked claim is never written to a ledger.

What it measures
  emit    every claim is read-with-a-reference or marked inferred (else nothing is written, REVIEW);
          with --repo every reference resolves to a real file:line (else written, REVIEW)
  verify  page: the claims ledger is the LAST section of the page and nothing teaching follows it;
          its rows (id, claim text, path:line, basis), its tally and the embedded #c8-claims JSON
          all equal the manifest. md: the table rows and the tally equal the manifest.
  video   a `<slug>.claims.md` sits beside the mp4 (and, with --claims, matches the manifest)

NOT measured: whether a cited line actually supports its claim (read it), whether "inferred" was
an honest call. Exit: 0 PASS · 1 REVIEW · 2 SKIPPED · 3 ERROR. Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE.parent))
from lib.common import EXIT_ERROR, EXIT_PASS, EXIT_REVIEW, EXIT_SKIPPED, emit  # noqa: E402
from facts_trace import is_inferred, resolve_ref  # noqa: E402

GATE = "check_claims"


# ------------------------------------------------------------------ manifest
def basis(c: dict) -> str:
    if is_inferred(c):
        return "inferred"
    st = str(c.get("status") or c.get("basis") or "").strip().lower()
    if str(c.get("ref") or "").strip() and st in ("", "read"):
        return "read"
    return "unmarked"


def norm_text(s) -> str:
    """Compare claim wording, not its markup: drop tags and the page's inline marks."""
    s = html.unescape(re.sub(r"<[^>]+>", " ", str(s or "")))
    s = re.sub(r"\[\[([^\]]+)\]\]", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)\s]+\)", r"\1", s)
    s = s.replace("`", "").replace("**", "")
    return re.sub(r"\s+", " ", s).strip()


def load_manifest(path: Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("claims") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("claims manifest has no claims[] list")
    out = []
    for i, c in enumerate(rows, 1):
        if not isinstance(c, dict):
            raise ValueError(f"claim #{i} is not an object")
        out.append({"id": str(c.get("id") or f"c{i}"), "text": str(c.get("text") or c.get("claim") or "").strip(),
                    "ref": str(c.get("ref") or "").strip(), "basis": basis(c)})
    return out


def tally_line(rows: list) -> str:
    n = len(rows)
    r = sum(1 for c in rows if c["basis"] == "read")
    return f"{n} claim{'s' if n != 1 else ''}: {r} read in the code, {n - r} inferred."


# ------------------------------------------------------------------ emit (video sidecar)
def md_cell(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").strip()


def render_md(rows: list, title: str) -> str:
    lines = [f"# Claims: {md_cell(title)}", "",
             "Each statement the video makes, the file and line it was read from, and whether it was read "
             "in the code or inferred. A video cannot be scanned; this file is how it is checked.", "",
             tally_line(rows), "",
             "| # | Claim | Where in the code | Basis |", "|---|---|---|---|"]
    for c in rows:
        where = f"`{md_cell(c['ref'])}`" if c["ref"] else "no single line"
        lines.append(f"| {md_cell(c['id'])} | {md_cell(c['text'])} | {where} | {c['basis']} |")
    return "\n".join(lines) + "\n"


def cmd_emit(a) -> int:
    mp = Path(a.claims)
    if not mp.exists():
        emit({"gate": GATE, "mode": "emit", "status": "SKIPPED", "summary": f"no claims manifest at {mp}"})
        return EXIT_SKIPPED
    try:
        rows = load_manifest(mp)
    except (OSError, ValueError) as ex:
        emit({"gate": GATE, "mode": "emit", "status": "ERROR", "summary": f"bad claims manifest: {ex}"})
        return EXIT_ERROR
    if not rows:
        emit({"gate": GATE, "mode": "emit", "status": "SKIPPED", "summary": "the manifest holds no claims"})
        return EXIT_SKIPPED
    unmarked = [c["id"] for c in rows if c["basis"] == "unmarked"]
    if unmarked:
        emit({"gate": GATE, "mode": "emit", "status": "REVIEW", "written": None, "unmarked_ids": unmarked,
              "rule": "C3.no_line_no_knowledge",
              "summary": f"{len(unmarked)} claim(s) have no path:line and are not marked inferred; nothing "
                         f"written (read more, or mark them inferred: true)"})
        return EXIT_REVIEW
    unresolved = []
    if a.repo:
        repo = Path(a.repo).resolve()
        for c in rows:
            if c["ref"]:
                ok, why = resolve_ref(repo, c["ref"])
                if not ok:
                    unresolved.append({"id": c["id"], "ref": c["ref"], "problem": why})
    out = Path(a.out)
    title = a.title or out.name.replace(".claims.md", "")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_md(rows, title), encoding="utf-8")
    n_inf = sum(1 for c in rows if c["basis"] == "inferred")
    emit({"gate": GATE, "mode": "emit", "status": "REVIEW" if unresolved else "PASS", "written": str(out),
          "claims": len(rows), "read": len(rows) - n_inf, "inferred": n_inf,
          "inferred_ids": [c["id"] for c in rows if c["basis"] == "inferred"], "unresolved": unresolved,
          "summary": (f"{len(unresolved)} reference(s) do not resolve; {out.name} written for review" if unresolved
                      else f"{out.name} written: {tally_line(rows)}")})
    return EXIT_REVIEW if unresolved else EXIT_PASS


# ------------------------------------------------------------------ verify: page
class LedgerParser(HTMLParser):
    """Collects the page's top-level sections (in order), the rows/tally of the ledger inside each,
    and the embedded #c8-claims JSON."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections: list = []      # {id, has_ledger, rows: [...], tally}
        self.depth = 0
        self.sec_depth = None
        self.row = None
        self.cell = None
        self.cells: list = []
        self.tally = None
        self.tally_buf: list = []
        self.json_buf = None
        self.json_text = None
        self.after_main = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("br", "img", "input", "hr", "meta", "link", "wbr", "col", "source", "track"):
            return
        self.depth += 1
        if tag == "section" and "data-c8-section" in a and self.sec_depth is None:
            self.sections.append({"id": a.get("id"), "has_ledger": False, "rows": [], "tally": None})
            self.sec_depth = self.depth
        if self.sec_depth is not None and "data-c8-claims" in a:
            self.sections[-1]["has_ledger"] = True
        if self.sec_depth is not None and tag == "tr" and "data-claim-id" in a:
            self.row = {"id": a.get("data-claim-id"), "basis": a.get("data-claim-status"),
                        "ref": a.get("data-claim-ref") or ""}
            self.cells = []
        if self.row is not None and tag == "td":
            self.cell = []
        if self.sec_depth is not None and "data-c8-claims-tally" in a:
            self.tally = self.depth
            self.tally_buf = []
        if tag == "script" and a.get("id") == "c8-claims":
            self.json_buf = []

    def handle_endtag(self, tag):
        if tag in ("br", "img", "input", "hr", "meta", "link", "wbr", "col", "source", "track"):
            return
        if self.json_buf is not None and tag == "script":
            self.json_text = "".join(self.json_buf)
            self.json_buf = None
        if self.row is not None and tag == "td" and self.cell is not None:
            self.cells.append("".join(self.cell))
            self.cell = None
        if self.row is not None and tag == "tr":
            self.row["text"] = self.cells[0] if self.cells else ""
            self.sections[-1]["rows"].append(self.row)
            self.row = None
        if self.tally is not None and self.depth == self.tally:
            self.sections[-1]["tally"] = re.sub(r"\s+", " ", "".join(self.tally_buf)).strip()
            self.tally = None
        if self.sec_depth is not None and self.depth == self.sec_depth and tag == "section":
            self.sec_depth = None
        self.depth -= 1

    def handle_data(self, data):
        if self.json_buf is not None:
            self.json_buf.append(data)
        if self.cell is not None:
            self.cell.append(data)
        if self.tally is not None:
            self.tally_buf.append(data)


def compare(manifest: list, found: list, where: str) -> list:
    out = []
    mi = {c["id"]: c for c in manifest}
    fi = {}
    for c in found:
        if c["id"] in fi:
            out.append({"where": where, "id": c["id"], "problem": "listed twice"})
        fi[c["id"]] = c
    for cid in mi:
        if cid not in fi:
            out.append({"where": where, "id": cid, "problem": "in the manifest but not in the ledger"})
    for cid in fi:
        if cid not in mi:
            out.append({"where": where, "id": cid, "problem": "in the ledger but not in the manifest"})
    for cid, m in mi.items():
        f = fi.get(cid)
        if not f:
            continue
        if norm_text(f.get("text")) != norm_text(m["text"]):
            out.append({"where": where, "id": cid, "problem": "claim text differs",
                        "ledger": norm_text(f.get("text"))[:100], "manifest": norm_text(m["text"])[:100]})
        if str(f.get("ref") or "").strip() != m["ref"]:
            out.append({"where": where, "id": cid, "problem": "path:line differs",
                        "ledger": f.get("ref"), "manifest": m["ref"]})
        if str(f.get("basis") or "").strip().lower() != m["basis"]:
            out.append({"where": where, "id": cid, "problem": "read/inferred basis differs",
                        "ledger": f.get("basis"), "manifest": m["basis"]})
    if [c["id"] for c in found if c["id"] in mi] != [c["id"] for c in manifest if c["id"] in fi]:
        out.append({"where": where, "problem": "claims are listed in a different order than the manifest"})
    return out


def verify_page(text: str, manifest: list) -> tuple:
    p = LedgerParser()
    p.feed(text)
    p.close()
    findings = []
    secs = p.sections
    ledgers = [i for i, s in enumerate(secs) if s["has_ledger"]]
    facts = {"sections": len(secs), "ledger_sections": [secs[i]["id"] for i in ledgers]}
    if not secs:
        findings.append({"problem": "no data-c8-section sections found: is this a Coordn8 page?"})
        return findings, facts
    last = secs[-1]
    if not last["has_ledger"]:
        findings.append({"problem": "the page does not END with the claims ledger",
                         "last_section": last["id"], "rule": "C3.ledger_last"})
        return findings, facts
    facts["ledger_id"] = last["id"]
    facts["rows"] = len(last["rows"])
    findings += compare(manifest, last["rows"], "page ledger")
    want_tally = tally_line(manifest) if manifest else None
    if want_tally and last["tally"] != want_tally:
        findings.append({"where": "page ledger", "problem": "tally differs", "ledger": last["tally"],
                         "expected": want_tally})
    if p.json_text is None:
        findings.append({"where": "#c8-claims JSON", "problem": "the page embeds no #c8-claims JSON"})
    else:
        try:
            emb = json.loads(p.json_text)
            rows = [{"id": str(c.get("id")), "text": c.get("text"), "ref": str(c.get("ref") or ""),
                     "basis": str(c.get("status") or "")} for c in emb.get("claims") or []]
            findings += compare(manifest, rows, "#c8-claims JSON")
        except ValueError as ex:
            findings.append({"where": "#c8-claims JSON", "problem": f"unparseable: {ex}"})
    return findings, facts


# ------------------------------------------------------------------ verify: md
_MD_ROW = re.compile(r"^\|\s*(?P<id>(?:[^|\\]|\\.)+?)\s*\|\s*(?P<text>(?:[^|\\]|\\.)*?)\s*\|\s*(?P<where>(?:[^|\\]|\\.)*?)\s*"
                     r"\|\s*(?P<basis>read|inferred|unmarked)\s*\|\s*$")


def _unmd(s: str) -> str:
    return re.sub(r"\\(.)", r"\1", s)


def verify_md(text: str, manifest: list) -> tuple:
    rows = []
    tally = None
    for ln in text.splitlines():
        m = _MD_ROW.match(ln.strip())
        if m and m.group("id") not in ("#", "---"):
            where = _unmd(m.group("where")).strip()
            ref = where[1:-1] if where.startswith("`") and where.endswith("`") else ""
            rows.append({"id": _unmd(m.group("id")), "text": _unmd(m.group("text")), "ref": ref,
                         "basis": m.group("basis")})
        elif re.match(r"^\d+ claims?: \d+ read in the code, \d+ inferred\.$", ln.strip()):
            tally = ln.strip()
    findings = compare(manifest, rows, "claims.md")
    if manifest and tally != tally_line(manifest):
        findings.append({"where": "claims.md", "problem": "tally differs", "ledger": tally,
                         "expected": tally_line(manifest)})
    return findings, {"rows": len(rows)}


def cmd_verify(a) -> int:
    target, mp = Path(a.target), Path(a.claims)
    for p in (target, mp):
        if not p.exists():
            emit({"gate": GATE, "mode": "verify", "status": "SKIPPED", "summary": f"missing {p}"})
            return EXIT_SKIPPED
    try:
        manifest = load_manifest(mp)
        text = target.read_text(encoding="utf-8")
    except (OSError, ValueError) as ex:
        emit({"gate": GATE, "mode": "verify", "status": "ERROR", "summary": str(ex)})
        return EXIT_ERROR
    is_md = target.suffix.lower() in (".md", ".markdown")
    findings, facts = (verify_md if is_md else verify_page)(text, manifest)
    unmarked = [c["id"] for c in manifest if c["basis"] == "unmarked"]
    for cid in unmarked:
        findings.append({"id": cid, "problem": "the manifest claim has no path:line and is not marked inferred",
                         "rule": "C3.no_line_no_knowledge"})
    if a.repo:
        repo = Path(a.repo).resolve()
        for c in manifest:
            if c["ref"]:
                ok, why = resolve_ref(repo, c["ref"])
                if not ok:
                    findings.append({"id": c["id"], "ref": c["ref"], "problem": why})
    n_inf = sum(1 for c in manifest if c["basis"] == "inferred")
    emit({"gate": GATE, "mode": "verify", "status": "REVIEW" if findings else "PASS", "target": str(target),
          "kind": "claims.md" if is_md else "page", "claims": len(manifest), "inferred": n_inf,
          "read": sum(1 for c in manifest if c["basis"] == "read"), "findings": findings, **facts,
          "summary": (f"{len(findings)} difference(s) between the ledger and the manifest" if findings else
                      f"the ledger matches the manifest: {tally_line(manifest) if manifest else 'no claims'}"),
          "advisory_note": "A matching ledger proves the page says what the manifest says, NOT that each "
                           "cited line supports its claim. Read the load-bearing ones."})
    return EXIT_REVIEW if findings else EXIT_PASS


# ------------------------------------------------------------------ video sidecar present
def cmd_video(a) -> int:
    mp4 = Path(a.mp4)
    side = mp4.with_name(mp4.stem + ".claims.md")
    if not side.exists():
        emit({"gate": GATE, "mode": "video", "status": "REVIEW", "expected": str(side), "rule": "C3.video_sidecar",
              "summary": f"{mp4.name} ships without {side.name}: write it with `check_claims.py emit`"})
        return EXIT_REVIEW
    if a.claims:
        a.target = str(side)
        return cmd_verify(a)
    emit({"gate": GATE, "mode": "video", "status": "PASS", "sidecar": str(side),
          "summary": f"{side.name} sits beside {mp4.name}"})
    return EXIT_PASS


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C3 claims ledger: emit the video sidecar, verify a ledger.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit", help="write <slug>.claims.md from a claims manifest")
    e.add_argument("claims")
    e.add_argument("--out", required=True)
    e.add_argument("--title")
    e.add_argument("--repo")
    v = sub.add_parser("verify", help="prove a page ledger or a claims.md matches the manifest")
    v.add_argument("target")
    v.add_argument("claims")
    v.add_argument("--repo")
    d = sub.add_parser("video", help="prove <slug>.claims.md sits beside <slug>.mp4")
    d.add_argument("mp4")
    d.add_argument("--claims")
    d.add_argument("--repo")
    a = ap.parse_args(argv)
    return {"emit": cmd_emit, "verify": cmd_verify, "video": cmd_video}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
