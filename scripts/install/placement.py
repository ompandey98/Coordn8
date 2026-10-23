#!/usr/bin/env python3
"""Coordn8 installer — the package step: put package-owned files where each host reads them,
merge into user files without ever overwriting them, and keep the installed.json ledger.

Ownership, decided per destination file (J2/J3):

  PACKAGE-OWNED  a file Coordn8 created: replaced freely on every install, removed by
                 uninstall. Recorded in installed.json "manifest" {abs path: sha256}.
  FOREIGN        a file that is (or may be) the user's: backed up to <user_home>/backups/,
                 then ONLY a delimited Coordn8 block inside it is inserted or replaced, via
                 scripts/lib/safe_write.py. Recorded in installed.json "blocks". Uninstall
                 removes the block and nothing else.

Rules per target:
  claude    CLAUDE_HOME/skills/coordn8/SKILL.md + CLAUDE_HOME/commands/coordn8.md (from
            adapters/claude-code/), every file under the package's skills/ tree, and
            agents/coordn8-*.md. Split-layout leftovers are removed.
  cursor    <project>/.cursor/rules/coordn8.mdc
  kiro      <project>/.kiro/steering/coordn8.md
  generic   <project>/COORDN8_PROMPT.md
            For these three: absent or provably ours -> replace; anything else -> back up
            and block-merge.
  codex     <project>/AGENTS.md is ALWAYS block-merged (never overwritten). The single
            exception is a file that is provably nothing but an older Coordn8 adapter (no
            user text at all): it is backed up, then rewritten as the block form, so the
            brain is not embedded twice.

A destination that already exists and is not byte-identical to the last recorded install
is backed up before it is replaced — so no file anyone may have edited is ever lost.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
from lib import safe_write  # noqa: E402
from lib.paths import claude_home, user_home  # noqa: E402

BRAIN_BEGIN = "<!-- COORDN8:BRAIN:BEGIN -->"
BRAIN_END = "<!-- COORDN8:BRAIN:END -->"
BLOCK_BEGIN_RE = re.compile(r"^(<!-- |# )COORDN8:BEGIN v", re.M)
INSTALLED_SCHEMA = 1
TARGETS = ("claude", "cursor", "codex", "kiro", "generic")
PROJECT_TARGETS = {
    "cursor": ("adapters/cursor/coordn8.mdc", ".cursor/rules/coordn8.mdc"),
    "kiro": ("adapters/kiro/coordn8.md", ".kiro/steering/coordn8.md"),
    "generic": ("adapters/generic/COORDN8_PROMPT.md", "COORDN8_PROMPT.md"),
    "codex": ("adapters/codex/AGENTS.md", "AGENTS.md"),
}
CLAUDE_LEFTOVERS = ("skills/coordn8/PLAYBOOK.md", "commands/coordn8-brain.md")
SKIP_NAMES = {".DS_Store", "Thumbs.db"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}


# ----------------------------------------------------------------------- basics
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str | None:
    try:
        return sha256_bytes(Path(p).read_bytes())
    except OSError:
        return None


def ap(p) -> str:
    """One canonical string form for every recorded path (absolute, not symlink-resolved)."""
    return os.path.abspath(os.path.expanduser(str(p)))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".coordn8-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def installed_path() -> Path:
    return user_home() / "installed.json"


def load_installed() -> dict | None:
    p = installed_path()
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_installed(data: dict) -> Path:
    p = installed_path()
    _atomic_write_bytes(p, (json.dumps(data, indent=1, sort_keys=False) + "\n").encode("utf-8"))
    return p


def package_version(pkg: Path) -> str:
    try:
        return (pkg / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def package_commit(pkg: Path) -> str:
    """The commit this copy came from: a git checkout's HEAD, else the commit npm resolved
    the github: spec to (recorded in the npx install's lockfile), else 'unknown'."""
    if (pkg / ".git").exists():
        try:
            r = subprocess.run(["git", "-C", str(pkg), "rev-parse", "HEAD"], capture_output=True,
                               text=True, timeout=5, stdin=subprocess.DEVNULL)
            s = (r.stdout or "").strip()
            if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,40}", s):
                return s
        except (OSError, subprocess.SubprocessError):
            pass
    for lock in (pkg.parent / ".package-lock.json", pkg.parent.parent / "package-lock.json"):
        try:
            data = json.loads(lock.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key, meta in (data.get("packages") or {}).items():
            if key.endswith("node_modules/coordn8") and isinstance(meta, dict):
                m = re.search(r"#([0-9a-f]{40})", str(meta.get("resolved", "")))
                if m:
                    return m.group(1)
    try:
        gh = json.loads((pkg / "package.json").read_text(encoding="utf-8")).get("gitHead")
        if isinstance(gh, str) and re.fullmatch(r"[0-9a-f]{7,40}", gh):
            return gh
    except (OSError, ValueError, AttributeError):
        pass
    return "unknown"


# ----------------------------------------------------------------- the ledger
class Ledger:
    """What this run installed. Merged with the previous installed.json at the end."""

    def __init__(self, prev: dict | None):
        prev = prev or {}
        self.prev_manifest: dict = dict(prev.get("manifest") or {})
        self.prev_targets: dict = dict(prev.get("file_targets") or {})
        self.prev_blocks: list = list(prev.get("blocks") or [])
        self.prev_json: list = list(prev.get("json_keys") or [])
        self.prev_dirs: list = list(prev.get("created_dirs") or [])
        self.files: dict = {}        # abs path -> {"sha", "target"}
        self.blocks: dict = {}       # abs path -> {"style", "target", "created"}
        self.json_keys: list = []
        self.created_dirs: list = []
        self.backups: list = []
        self.removed: list = []
        self.notes: list = []

    # -- helpers
    def backup(self, p: Path, why: str) -> str | None:
        bk = safe_write.backup(p)
        if bk:
            self.backups.append({"file": ap(p), "backup": str(bk), "why": why})
            return str(bk)
        return None

    def mkdirs(self, d: Path, root: Path) -> None:
        """mkdir -p, remembering every directory this run created below `root`."""
        d = Path(ap(d))
        root = Path(ap(root))
        missing = []
        cur = d
        while not cur.exists() and cur != cur.parent:
            missing.append(cur)
            cur = cur.parent
        d.mkdir(parents=True, exist_ok=True)
        for m in missing:
            try:
                m.relative_to(root)
            except ValueError:
                continue
            if m != root and ap(m) not in self.created_dirs:
                self.created_dirs.append(ap(m))

    def merged(self, steps_records: list[dict]) -> dict:
        """files / blocks / json_keys / dirs for installed.json: this run + every previous entry
        this run did not supersede and that still exists (other projects, other targets)."""
        manifest = {}
        targets = {}
        for p, sha in self.prev_manifest.items():
            if p in self.files or p in self.blocks or p in self.removed:
                continue
            if os.path.isfile(p):
                manifest[p] = sha
                targets[p] = self.prev_targets.get(p, "?")
        for p, meta in self.files.items():
            manifest[p] = meta["sha"]
            targets[p] = meta["target"]
        blocks = {}
        for b in self.prev_blocks:
            f = b.get("file")
            if not f or f in self.files or f in self.blocks:
                continue
            if os.path.isfile(f) and _has_block(Path(f), b.get("style", "html")):
                blocks[f] = b
        for f, meta in self.blocks.items():
            blocks[f] = dict(meta, file=f)
        jk = {(j.get("file"), j.get("key")): j for j in self.prev_json if j.get("file") and j.get("key")}
        for rec in steps_records:
            for j in rec.get("json_keys") or []:
                if isinstance(j, dict) and j.get("file") and j.get("key"):
                    jk[(ap(j["file"]), j["key"])] = {"file": ap(j["file"]), "key": str(j["key"]),
                                                     "step": rec.get("_step")}
            for b in rec.get("blocks") or []:
                if isinstance(b, dict) and b.get("file"):
                    f = ap(b["file"])
                    blocks.setdefault(f, {"file": f, "style": b.get("style", "html"),
                                          "target": rec.get("_step"), "created": False})
            for p, sha in (rec.get("files") or {}).items():
                if isinstance(p, str) and isinstance(sha, str):
                    manifest[ap(p)] = sha
                    targets[ap(p)] = rec.get("_step") or "?"
        dirs = [d for d in self.prev_dirs if os.path.isdir(d)]
        for d in self.created_dirs:
            if d not in dirs:
                dirs.append(d)
        return {"manifest": manifest, "file_targets": targets, "blocks": list(blocks.values()),
                "json_keys": list(jk.values()), "created_dirs": dirs}


def _has_block(p: Path, style: str = "html") -> bool:
    try:
        lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
        return safe_write._find_block(lines, style)[0] is not None
    except (OSError, safe_write.SafeWriteError, UnicodeDecodeError):
        return False


# ------------------------------------------------------- is a file provably ours?
def _norm(s: str) -> str:
    return re.sub(r"v\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.]+)?", "v#", s).replace("\r\n", "\n")


def classify(dest: Path, src_text: str, prev_sha: str | None) -> str:
    """absent | identical | ours-previous | ours-legacy | user.

    ours-legacy = a whole earlier Coordn8 adapter file: the same wrapper text (version numbers
    aside) before the brain fence, the brain fence, and nothing after it. Anything else that
    exists — including one of our files with a single line added by the user — is 'user'."""
    if not dest.exists():
        return "absent"
    cur_sha = sha256_file(dest)
    if cur_sha == sha256_bytes(src_text.encode("utf-8")):
        return "identical"
    if prev_sha and cur_sha == prev_sha:
        return "ours-previous"
    try:
        cur = dest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "user"
    if BLOCK_BEGIN_RE.search(cur):
        return "user"  # a merged file: user content + our block
    if cur.count(BRAIN_BEGIN) == 1 and cur.count(BRAIN_END) == 1 and BRAIN_BEGIN in src_text:
        head, rest = cur.split(BRAIN_BEGIN, 1)
        _, tail = rest.split(BRAIN_END, 1)
        src_head = src_text.split(BRAIN_BEGIN, 1)[0]
        if tail.strip() == "" and _norm(head) == _norm(src_head):
            return "ours-legacy"
    return "user"


# --------------------------------------------------------------- placement
def place_owned(dest: Path, data: bytes, target: str, root: Path, led: Ledger) -> str:
    """Write a package-owned file. Backs up an existing file unless it is byte-identical to
    what we wrote last time (or to what we are about to write)."""
    key = ap(dest)
    new_sha = sha256_bytes(data)
    action = "created"
    if dest.exists():
        cur = sha256_file(dest)
        if cur == new_sha:
            action = "unchanged"
        else:
            action = "replaced"
            if cur != led.prev_manifest.get(key):
                led.backup(dest, f"replaced by the {target} install")
    if action != "unchanged":
        led.mkdirs(dest.parent, root)
        _atomic_write_bytes(dest, data)
    led.files[key] = {"sha": new_sha, "target": target}
    return action


def merge_foreign(dest: Path, text: str, version: str, target: str, root: Path, led: Ledger,
                  created: bool | None = None) -> dict:
    existed = dest.exists()
    if not existed:
        led.mkdirs(dest.parent, root)
    original = dest.read_text(encoding="utf-8") if existed else None
    res = safe_write.merge_block(dest, text, version, "html")
    if res.get("backup"):
        led.backups.append({"file": ap(dest), "backup": res["backup"], "why": f"{target} block merge"})
    prior = next((b for b in led.prev_blocks if b.get("file") == ap(dest)), None)
    was_created = (not existed) if created is None else bool(created)
    if prior is not None and prior.get("created"):
        was_created = True  # we created this file in an earlier run; still informational only
    entry = {"style": "html", "target": target, "created": was_created}
    if prior is not None and "user_eol" in prior:
        entry.update({k: prior[k] for k in ("user_sha", "user_eol") if k in prior})
    elif original and not BLOCK_BEGIN_RE.search(original):
        # remember how the user's file ended, so uninstall can give it back byte-for-byte
        entry.update({"user_sha": sha256_bytes(original.encode("utf-8")), "user_eol": original.endswith("\n")})
    led.blocks[ap(dest)] = entry
    return res


def remove_foreign_block(b: dict) -> dict:
    """Remove our block from a user file. safe_write appends a newline separator when it merges
    into a file that did not end with one; if the file is otherwise exactly the user's original
    (sha recorded at merge time), that one separator byte is taken back out too."""
    f = Path(b["file"])
    res = safe_write.remove_block(f, b.get("style", "html"))
    if res.get("action") == "block-removed" and b.get("user_eol") is False and f.is_file():
        cur = f.read_text(encoding="utf-8")
        if cur.endswith("\n") and sha256_bytes(cur[:-1].encode("utf-8")) == b.get("user_sha"):
            safe_write._atomic_write(f, cur[:-1])
            res["action"] = "block-removed"
            res["eol_restored"] = True
    return res


def claude_plan(pkg: Path) -> tuple[dict, list[str]]:
    """{dest: src} for the Claude target. Generic over the package's skills/ and agents/ dirs."""
    ch = claude_home()
    plan: dict = {}
    missing = []
    skills = pkg / "skills"
    if skills.is_dir():
        for f in sorted(skills.rglob("*")):
            rel = f.relative_to(skills)
            if len(rel.parts) < 2:
                continue  # skills/README.md and other loose files are package docs, not skills
            if f.is_file() and f.name not in SKIP_NAMES and not any(p in SKIP_DIRS for p in rel.parts) \
                    and not f.name.endswith(".pyc"):
                plan[ch / "skills" / rel] = f
    agents = pkg / "agents"
    if agents.is_dir():
        for f in sorted(agents.glob("coordn8-*.md")):
            if f.is_file():
                plan[ch / "agents" / f.name] = f
    # the monolithic brain-embedded adapter files win over anything else at the same path
    for src_rel, dst_rel in (("adapters/claude-code/SKILL.md", "skills/coordn8/SKILL.md"),
                             ("adapters/claude-code/commands/coordn8.md", "commands/coordn8.md")):
        src = pkg / src_rel
        if src.is_file():
            plan[ch / dst_rel] = src
        else:
            missing.append(src_rel)
    return plan, missing


def install_claude(pkg: Path, led: Ledger, say) -> bool:
    ch = claude_home()
    plan, missing = claude_plan(pkg)
    if missing:
        say(f"Claude Code: FAILED — adapter file(s) missing from the package: {', '.join(missing)}")
        return False
    ch.mkdir(parents=True, exist_ok=True)
    for rel in CLAUDE_LEFTOVERS:
        p = ch / rel
        if p.is_file():
            if sha256_file(p) != led.prev_manifest.get(ap(p)):
                led.backup(p, "split-layout leftover removed")
            p.unlink()
            led.removed.append(ap(p))
            say(f"Claude Code: removed stale split-layout leftover {p}")
    counts = {"created": 0, "replaced": 0, "unchanged": 0}
    for dest, src in plan.items():
        counts[place_owned(dest, src.read_bytes(), "claude", ch, led)] += 1
    # prune package-owned Claude files a previous version installed but this one no longer ships
    for p, tgt in led.prev_targets.items():
        if tgt != "claude" or p in led.files or not os.path.isfile(p):
            continue
        try:
            Path(p).relative_to(Path(ap(ch)))
        except ValueError:
            continue  # recorded under a different CLAUDE_HOME: not ours to prune from here
        if sha256_file(Path(p)) == led.prev_manifest.get(p):
            os.unlink(p)
            led.removed.append(p)
            say(f"Claude Code: removed {p} (no longer shipped by this version)")
        else:
            led.notes.append(f"{p} was installed by an earlier version and has since been edited; left in place")
    extra = len(plan) - 2
    say(f"Claude Code: skill -> {ch / 'skills' / 'coordn8'}/  |  command -> /coordn8"
        + (f"  |  +{extra} more package file(s) (skills/agents)" if extra > 0 else "")
        + f"  [{counts['created']} new, {counts['replaced']} updated, {counts['unchanged']} unchanged]")
    install_claude_hooks(pkg, ch, led, say)
    return True


def install_claude_hooks(pkg: Path, ch: Path, led, say) -> None:
    """Non-plugin route: merge the four Coordn8 hooks into CLAUDE_HOME/settings.json (backup,
    then only Coordn8's own matcher groups change). A failure here never fails the install;
    opt-in with COORDN8_HOOKS=1. (The plugin route reads hooks/hooks.json itself.)"""
    if not (pkg / "hooks" / "hooks.json").is_file():
        return
    if os.environ.get("COORDN8_HOOKS") != "1":
        # opt-in on this route: the user's settings.json is user-owned (J2). The plugin route
        # loads the hooks with no settings edit at all.
        say("Claude Code: hooks are loaded automatically by the plugin install; on this route enable them with "
            f"COORDN8_HOOKS=1 (or: python3 {pkg / 'scripts/install/hooks_settings.py'} merge --settings {ch / 'settings.json'})")
        return
    try:
        from install import hooks_settings
        res = hooks_settings.merge_settings(ch / "settings.json", pkg)
        if res.get("ok"):
            say(f"Claude Code: hooks -> {ch / 'settings.json'} ({', '.join(res.get('events') or []) or 'unchanged'})")
        else:
            led.notes.append(f"hooks not merged into {ch / 'settings.json'}: {res}")
            say(f"Claude Code: hooks SKIPPED — {res.get('reason') or res}")
    except Exception as e:  # never fatal
        led.notes.append(f"hooks not merged into {ch / 'settings.json'}: {e}")
        say(f"Claude Code: hooks SKIPPED — {e} (the skill, command and agents are installed)")


def install_project_target(target: str, pkg: Path, project: Path, version: str, led: Ledger, say) -> bool:
    src_rel, dst_rel = PROJECT_TARGETS[target]
    src = pkg / src_rel
    if not src.is_file():
        say(f"{target}: FAILED — adapter file missing from the package: {src_rel}")
        return False
    text = src.read_text(encoding="utf-8")
    dest = project / dst_rel
    key = ap(dest)
    prev_sha = led.prev_manifest.get(key)
    kind = classify(dest, text, prev_sha)
    label = {"cursor": "Cursor", "kiro": "Kiro", "generic": "Generic", "codex": "Codex"}[target]
    try:
        if target == "codex":
            if kind in ("ours-legacy", "identical", "ours-previous"):
                # provably nothing but an older Coordn8 adapter: back it up, rewrite as the block form
                led.backup(dest, "whole-file Coordn8 AGENTS.md converted to the block form")
                dest.unlink()
                merge_foreign(dest, text, version, target, project, led, created=True)
                say(f"{label}: {dest} was an older Coordn8-only file — backed up and converted to a "
                    "Coordn8 block")
            else:
                res = merge_foreign(dest, text, version, target, project, led)
                verb = {"created": "created with a Coordn8 block", "merged": "Coordn8 block merged "
                        "(your content untouched, backup taken)", "unchanged": "Coordn8 block already current"}
                say(f"{label}: {dest} — {verb.get(res['action'], res['action'])}")
            return True
        if kind == "user":
            res = merge_foreign(dest, text, version, target, project, led)
            if key in led.prev_manifest:
                led.notes.append(f"{dest} had been edited since the last install; it is now treated as "
                                 "your file and only a Coordn8 block is maintained in it")
            outside = dest.read_text(encoding="utf-8").split("<!-- COORDN8:BEGIN v", 1)[0]
            if BRAIN_BEGIN in outside:
                led.notes.append(f"{dest} still contains an older Coordn8 brain outside the block — "
                                 "remove it by hand if you no longer want it (backup taken)")
            say(f"{label}: {dest} is your file — Coordn8 block merged, nothing else changed")
            return True
        action = place_owned(dest, text.encode("utf-8"), target, project, led)
        say(f"{label}: {dst_rel} -> {dest} ({action})")
        return True
    except (safe_write.SafeWriteError, OSError) as e:
        say(f"{label}: FAILED — {e}")
        return False


def regenerate_adapters(pkg: Path, timeout: float) -> tuple[bool, str]:
    gen = pkg / "build" / "generate_adapters.py"
    if not gen.is_file():
        return False, "build/generate_adapters.py not present — using the adapters already in the package"
    try:
        r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, cwd=str(pkg))
    except subprocess.TimeoutExpired:
        return False, f"adapter regeneration timed out after {timeout:g}s — using existing adapters"
    except OSError as e:
        return False, f"adapter regeneration could not start ({e}) — using existing adapters"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [f"exit {r.returncode}"]
        return False, f"adapter regeneration failed ({tail[0]}) — using existing adapters"
    return True, f"adapters regenerated from PLAYBOOK.md (v{package_version(pkg)})"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
