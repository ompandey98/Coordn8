#!/usr/bin/env python3
"""Coordn8 installer — one command that installs, updates and uninstalls, never gets stuck,
and never loses a user's file.

    installer.py init      [TARGET] [--target claude|cursor|codex|kiro|generic|all]
                           [--project-dir DIR] [--no-deps] [--no-voices] [--no-mcp]
                           [--no-refs] [--no-signin] [--steps a,b] [--yes] [--quiet]
    installer.py update    (same as init)
    installer.py uninstall [--purge [--yes]] [--project-dir DIR]
    installer.py status    [--json]

init / update run these steps strictly in order. Each later step is a separate script run as
a time-bounded subprocess with no stdin; a failing, missing or timed-out step is reported
with its reason and the run continues:

    package  regenerate adapters, install package-owned files, block-merge user files,
             write <user_home>/installed.json
    deps     scripts/setup_env.py           (tools and libraries)
    voices   scripts/voice/voices.py install
    mcp      scripts/mcp/install_mcp.py install
    refs     scripts/refs/refresh.py
    signin   scripts/mcp/install_mcp.py offer --timeout 20   (always last)

Exit 0 when the package step succeeded (later failures are reported, not fatal).
After a successful package step, ONLY Coordn8's own cached copies are removed from the npx
cache, so the next `npx -y github:ompandey98/coordn8 init` fetches the newest commit.

Test/ops knobs: COORDN8_FAIL_STEP=<name>[,<name>] forces a step to fail;
COORDN8_STEP_TIMEOUT=<s> (or COORDN8_STEP_TIMEOUT_<NAME>) overrides step timeouts.
User data under <user_home> is never modified: only installed.json and backups/ are written,
and missing directories created.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from install import npx_cache, placement, steps  # noqa: E402
from lib import safe_write  # noqa: E402
from lib.paths import PKG_ROOT, claude_home, ensure_user_home, user_home  # noqa: E402

ARROW = "→"


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower()
        try:
            ARROW.encode(enc or "ascii")
        except (LookupError, UnicodeEncodeError):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def say(msg: str) -> None:
    print(f"[coordn8] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"[coordn8] WARNING: {msg}", flush=True)


# ------------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="installer.py", description="Coordn8 installer")
    sub = ap.add_subparsers(dest="cmd")
    for name in ("init", "update", "upgrade"):
        p = sub.add_parser(name, help="install or update to this package's version")
        p.add_argument("target_pos", nargs="?", default=None, metavar="TARGET")
        p.add_argument("--target", default=None,
                       help="claude|cursor|codex|kiro|generic|all (comma-separated allowed; default all)")
        p.add_argument("--project-dir", default=None, help="where cursor/codex/kiro/generic files go (default: cwd)")
        p.add_argument("--no-deps", action="store_true")
        p.add_argument("--no-voices", action="store_true")
        p.add_argument("--no-mcp", action="store_true")
        p.add_argument("--no-refs", action="store_true")
        p.add_argument("--no-signin", action="store_true")
        p.add_argument("--steps", default=None, help="run only these steps, e.g. package,deps")
        p.add_argument("--yes", "-y", action="store_true",
                       help="fully unattended: the sign-in offer never reads the terminal either")
        p.add_argument("--quiet", action="store_true", help="do not echo step output")
    u = sub.add_parser("uninstall", help="remove package-owned files and inserted Coordn8 blocks")
    u.add_argument("--purge", action="store_true", help="ALSO delete the user home (needs --yes)")
    u.add_argument("--yes", "-y", action="store_true")
    u.add_argument("--project-dir", default=None, help="for installs that predate installed.json")
    s = sub.add_parser("status", help="what is installed, and is it intact")
    s.add_argument("--json", action="store_true")
    return ap


def parse_targets(raw: str | None) -> list[str]:
    raw = (raw or "all").strip().lower()
    out: list[str] = []
    for t in [x.strip() for x in raw.split(",") if x.strip()]:
        if t == "all":
            out.extend(placement.TARGETS)
        elif t in placement.TARGETS:
            out.append(t)
        else:
            raise ValueError(f"unknown target '{t}' (use claude|cursor|codex|kiro|generic|all)")
    seen = []
    for t in out:
        if t not in seen:
            seen.append(t)
    return seen


def parse_steps(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    sel = [x.strip().lower() for x in raw.split(",") if x.strip()]
    bad = [x for x in sel if x not in steps.STEP_ORDER]
    if bad:
        raise ValueError(f"unknown step(s) {bad}; steps are {','.join(steps.STEP_ORDER)}")
    return sel


# ----------------------------------------------------------------------- init
def package_step(args, targets: list[str], project: Path, prev: dict | None):
    """Returns (status, detail, ledger)."""
    led = placement.Ledger(prev)
    if "package" in steps.forced_failures():
        return "failed", "forced failure (COORDN8_FAIL_STEP=package)", led
    migration = migrate_user_data()
    led.migration = migration
    ok_regen, regen_msg = placement.regenerate_adapters(
        PKG_ROOT, steps.step_timeout("package", steps.PACKAGE_TIMEOUT))
    (say if ok_regen else warn)(regen_msg)
    version = placement.package_version(PKG_ROOT)
    results = {}
    for t in targets:
        try:
            if t == "claude":
                results[t] = placement.install_claude(PKG_ROOT, led, say)
            else:
                results[t] = placement.install_project_target(t, PKG_ROOT, project, version, led, say)
        except Exception as e:  # one broken target never aborts the others
            warn(f"{t}: FAILED — {e}")
            results[t] = False
    failed = [t for t, ok in results.items() if not ok]
    detail = (f"{len(led.files)} package file(s), {len(led.blocks)} merged block(s) "
              f"for {','.join(targets)}; {len(led.backups)} backup(s)")
    if not ok_regen:
        detail += "; adapters NOT regenerated"
    detail += f"; user data: {migration['detail']}"
    if failed:
        return "failed", f"target(s) failed: {','.join(failed)}; " + detail, led
    return "ok", detail, led


def migrate_user_data() -> dict:
    """Run scripts/memory/migrate.py right after ensure_user_home(): a no-op when nothing is
    pending, otherwise it backs the user home up first and migrates. Time-bounded, no stdin;
    a failure is reported and skipped, never fatal to the install."""
    rel = "scripts/memory/migrate.py"
    script = PKG_ROOT / rel
    if "migrate" in steps.forced_failures():
        res = {"status": "failed", "detail": "migration skipped: forced failure (COORDN8_FAIL_STEP=migrate)"}
    elif not script.is_file():
        res = {"status": "skipped", "detail": f"migration not in this build ({rel})"}
    else:
        timeout = steps.step_timeout("migrate", 120)
        env = dict(os.environ)
        env["COORDN8_NONINTERACTIVE"] = "1"
        r = steps.run_step("migrate", [sys.executable, str(script), "run", "--json"], timeout, env, False)
        text = "\n".join(r.get("lines") or [])
        data = None
        try:
            data = json.loads(text[text.index("{"):]) if "{" in text else None
        except ValueError:
            data = None
        if r["status"] == "ok" and isinstance(data, dict) and data.get("ok"):
            if data.get("steps"):
                moves = ", ".join(f"v{x.get('from')}→v{x.get('to')}" for x in data["steps"])
                res = {"status": "migrated", "detail": f"migrated {moves} (backup {data.get('backup')})",
                       "backup": data.get("backup"), "steps": data.get("steps")}
            else:
                res = {"status": "current", "detail": "schema current, nothing to migrate"}
        else:
            why = (data or {}).get("error") if isinstance(data, dict) else None
            res = {"status": "failed" if r["status"] != "timeout" else "timeout",
                   "detail": f"migration skipped: {why or r.get('detail') or r['status']} — your data was not changed"}
    (say if res["status"] in ("migrated", "current") else warn)(f"user data: {res['detail']}")
    return res


def record(prev, led, args, targets, project, step_rows, step_records) -> dict:
    merged = led.merged(step_records)
    data = {
        "schema": placement.INSTALLED_SCHEMA,
        "name": "coordn8",
        "version": placement.package_version(PKG_ROOT),
        "previous_version": (prev or {}).get("version"),
        "commit": placement.package_commit(PKG_ROOT),
        "date": placement.now_iso(),
        "package_root": str(PKG_ROOT),
        "targets": targets,
        "project_dir": str(project),
        "claude_home": str(claude_home()),
        **merged,
        "migration": getattr(led, "migration", None),
        "backups_this_run": led.backups,
        "notes": led.notes,
        "steps": step_rows,
    }
    placement.write_installed(data)
    return data


def step_command(name: str) -> tuple[list[str] | None, Path]:
    script_rel, args, _, _ = steps.STEP_DEFS[name]
    script = PKG_ROOT / script_rel
    cmd = [sys.executable, str(script), *args]
    if name == "deps":
        flag = steps.noninteractive_flag(script)
        if flag:
            cmd.append(flag)
    return cmd, script


def print_summary(rows: list[dict]) -> None:
    w1 = max(len("step"), *(len(r["step"]) for r in rows))
    w2 = max(len("status"), *(len(r["status"]) for r in rows))
    print("")
    print(f"  {'step'.ljust(w1)} | {'status'.ljust(w2)} | detail")
    print(f"  {'-' * w1}-+-{'-' * w2}-+-{'-' * 40}")
    for r in rows:
        print(f"  {r['step'].ljust(w1)} | {r['status'].ljust(w2)} | {r['detail']}")
    print("", flush=True)


def _install_lock():
    """One Coordn8 install at a time. Two at once (e.g. a paused run resumed next to a
    new one) race on the same files and the shared npx copy. A lock held by a dead
    process is stale and is taken over. Returns (ok, message, release)."""
    lock = user_home() / ".install.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.write(fd, str(os.getpid()).encode()); os.close(fd)
            return True, "", (lambda: lock.unlink() if lock.exists() else None)
        except FileExistsError:
            try:
                other = int((lock.read_text() or "0").strip() or 0)
            except (OSError, ValueError):
                other = 0
            alive = False
            if other > 0:
                try:
                    os.kill(other, 0); alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            if alive:
                return False, (f"another Coordn8 install is already running (pid {other}). Let it finish, "
                               f"or stop it (Ctrl-C in its window; `kill {other}` if it was paused with Ctrl-Z), "
                               "then run this again."), (lambda: None)
            try:
                lock.unlink()           # stale: its process is gone
            except OSError:
                pass
    return True, "", (lambda: None)


def cmd_init(args) -> int:
    ok, msg, release = _install_lock()
    if not ok:
        say(msg)
        return 3
    try:
        return _cmd_init(args)
    finally:
        try:
            release()
        except OSError:
            pass


def _cmd_init(args) -> int:
    try:
        targets = parse_targets(args.target or args.target_pos)
        selected = parse_steps(args.steps)
    except ValueError as e:
        warn(str(e))
        return 1
    project = Path(placement.ap(args.project_dir or os.getcwd()))
    prev = placement.load_installed()
    prev_version = (prev or {}).get("version")
    new_version = placement.package_version(PKG_ROOT)
    say(f"Coordn8 v{new_version} installer — targets: {','.join(targets)}"
        + (f" — project dir: {project}" if any(t != "claude" for t in targets) else ""))
    created = ensure_user_home()
    if created:
        say(f"user home ready at {user_home()} (created {len(created)} missing item(s); nothing existing touched)")
    forced = steps.forced_failures()
    tty = steps.stdin_is_tty()
    rows: list[dict] = []
    records: list[dict] = []
    package_ok = None
    led = None

    for name in steps.STEP_ORDER:
        if selected is not None and name not in selected:
            rows.append({"step": name, "status": "not selected", "detail": "not in --steps"})
            continue
        if name == "package":
            status, detail, led = package_step(args, targets, project, prev)
            package_ok = status == "ok"
            rows.append({"step": name, "status": status, "detail": detail})
            if package_ok:
                data = record(prev, led, args, targets, project, rows, records)
                pv = f"v{prev_version}" if prev_version else "(none)"
                tail = " (reinstalled, files refreshed)" if prev_version == new_version else ""
                say(f"installed {pv} {ARROW} v{new_version}{tail}")
                say(f"recorded {len(data['manifest'])} package file(s) and {len(data['blocks'])} "
                    f"block(s) in {placement.installed_path()}")
            continue
        script_rel, _, default_timeout, disablers = steps.STEP_DEFS[name]
        off = [d for d in disablers if getattr(args, d, False)]
        if off:
            rows.append({"step": name, "status": "disabled", "detail": f"--{off[0].replace('_', '-')}"})
            continue
        if name in forced:
            rows.append({"step": name, "status": "failed",
                         "detail": f"forced failure (COORDN8_FAIL_STEP={name}); skipped, install continued"})
            say(f"{name}: failed (forced) — continuing")
            continue
        cmd, script = step_command(name)
        if not script.is_file():
            rows.append({"step": name, "status": "skipped", "detail": f"not in this build ({script_rel})"})
            continue
        timeout = steps.step_timeout(name, default_timeout)
        interactive = name == "signin" and tty and not args.yes
        env = dict(os.environ)
        env.update({"COORDN8_INSTALL": "1", "COORDN8_NONINTERACTIVE": "0" if interactive else "1",
                    "COORDN8_INSTALL_TARGETS": ",".join(targets), "COORDN8_PROJECT_DIR": str(project),
                    "COORDN8_PKG_ROOT": str(PKG_ROOT), "PYTHONUNBUFFERED": "1"})
        say(f"{name}: running {script_rel} (timeout {timeout:g}s)")
        res = steps.run_step(name, cmd, timeout, env, interactive, echo=None if args.quiet else print,
                             cwd=str(project) if project.is_dir() else None)
        for rec in res["records"]:
            rec["_step"] = name
            records.append(rec)
        detail = res["detail"] or "done"
        if res["status"] not in ("ok",):
            detail = f"{detail}; install continued" if res["status"] in ("failed", "timeout") else detail
        rows.append({"step": name, "status": res["status"], "detail": f"{detail} ({res['seconds']}s)"})

    if package_ok and led is not None:
        record(prev, led, args, targets, project, rows, records)
        cache = npx_cache.clean()
        if cache["removed"]:
            say(f"force-update: removed {len(cache['removed'])} cached Coordn8 copy(ies) from "
                f"{cache['cache']}/_npx — nothing else in the npm cache was touched")
        else:
            say(f"force-update: no cached Coordn8 copy in {cache['cache']}/_npx (nothing else touched)")
        for e in cache["errors"]:
            warn(f"could not remove {e.get('path', '?')}: {e.get('error')}")
        for n in led.notes:
            say(f"note: {n}")
    print_summary(rows)
    if package_ok is None:
        say("package step not selected — nothing installed; installed.json unchanged")
        return 0
    if not package_ok:
        warn("the package step failed — Coordn8 is NOT (re)installed; installed.json and the npm cache untouched")
        return 1
    return 0


# ------------------------------------------------------------------ uninstall
def _within(child: Path, parent: Path) -> bool:
    return child == parent or str(child).startswith(str(parent).rstrip(os.sep) + os.sep)


def _home_is_unsafe_to_purge(h: Path) -> str | None:
    hr = Path(os.path.realpath(str(h)))
    home = Path(os.path.realpath(os.path.expanduser("~")))
    if hr == Path(hr.anchor) or len(hr.parts) < 3:
        return "it is a filesystem root or too short a path"
    if _within(home, hr):
        return "it is your home directory (or contains it)"
    if _within(Path(os.path.realpath(str(PKG_ROOT))), hr):
        return "it contains the Coordn8 package itself"
    if hr.name != ".coordn8" and not (hr / "schema.json").is_file() and not (hr / "installed.json").is_file():
        return "it does not look like a Coordn8 user home (no schema.json / installed.json)"
    return None


def _purge_listing(h: Path) -> tuple[list[str], int, int]:
    lines, nfiles, nbytes = [], 0, 0
    if not h.exists():
        return lines, 0, 0
    for child in sorted(h.iterdir()):
        cf, cb = 0, 0
        if child.is_dir() and not child.is_symlink():
            for root, _dirs, files in os.walk(str(child)):
                for f in files:
                    cf += 1
                    try:
                        cb += os.lstat(os.path.join(root, f)).st_size
                    except OSError:
                        pass
            lines.append(f"{child}/  ({cf} file(s), {cb} bytes)")
        else:
            cf = 1
            try:
                cb = os.lstat(str(child)).st_size
            except OSError:
                pass
            lines.append(f"{child}  ({cb} bytes)")
        nfiles += cf
        nbytes += cb
    return lines, nfiles, nbytes


def mcp_uninstall() -> dict:
    """Ask the MCP step to remove the server keys it added (it keeps its own record).
    Time-bounded, no stdin; a failure is reported, never fatal to the uninstall."""
    rel = "scripts/mcp/install_mcp.py"
    script = PKG_ROOT / rel
    if "mcp_uninstall" in steps.forced_failures():
        return {"status": "failed", "detail": "forced failure (COORDN8_FAIL_STEP=mcp_uninstall); not removed"}
    if not script.is_file():
        return {"status": "skipped", "detail": f"not in this build ({rel})"}
    env = dict(os.environ)
    env.update({"COORDN8_NONINTERACTIVE": "1", "COORDN8_UNINSTALL": "1"})
    r = steps.run_step("mcp-uninstall", [sys.executable, str(script), "uninstall"],
                       steps.step_timeout("mcp_uninstall", 180), env, False)
    if r["status"] == "ok":
        return {"status": "ok", "detail": f"removed the servers Coordn8 added ({r['detail'] or 'done'})"}
    return {"status": r["status"], "detail": f"{rel} uninstall {r['status']}: {r['detail']} — "
            "the rest of the uninstall continued"}


def _legacy_targets(project: Path) -> list[tuple[Path, str]]:
    ch = claude_home()
    return [(ch / "skills" / "coordn8" / "SKILL.md", "file"), (ch / "skills" / "coordn8" / "PLAYBOOK.md", "file"),
            (ch / "commands" / "coordn8.md", "file"), (ch / "commands" / "coordn8-brain.md", "file"),
            (project / ".cursor" / "rules" / "coordn8.mdc", "file"), (project / ".kiro" / "steering" / "coordn8.md", "file"),
            (project / "COORDN8_PROMPT.md", "file"), (project / "AGENTS.md", "agents")]


def cmd_uninstall(args) -> int:
    h = user_home()
    if args.purge:
        unsafe = _home_is_unsafe_to_purge(h) if h.exists() else None
        lines, nf, nb = _purge_listing(h)
        say(f"--purge would delete the Coordn8 user home {h} — {nf} file(s), {nb} bytes:")
        for ln in lines or ["(it does not exist)"]:
            print(f"    {ln}")
        if unsafe:
            warn(f"refusing to purge {h}: {unsafe}. Nothing was deleted.")
            return 2
        if not args.yes:
            say("nothing was deleted. This removes your memory, profiles, library, voices and backups "
                "for good. Re-run with:  uninstall --purge --yes")
            return 2

    mcp_un = mcp_uninstall()
    prev = placement.load_installed()
    removed, kept, blocks_removed, keys_removed, errors = [], [], [], [], []
    try:  # the hooks the non-plugin route merged into the host settings file
        from install import hooks_settings
        sp = claude_home() / "settings.json"
        if sp.is_file() and "/hooks/stop_gate.py" in sp.read_text(encoding="utf-8", errors="replace"):
            hr = hooks_settings.remove_settings(sp)
            if hr.get("events"):
                keys_removed.append(f"{sp} :: hooks ({', '.join(str(e) for e in hr['events'])})")
    except Exception as e:
        errors.append(f"hooks: {e}")
    if prev:
        for p, sha in sorted((prev.get("manifest") or {}).items()):
            fp = Path(p)
            if not fp.is_file():
                continue
            try:
                if placement.sha256_file(fp) != sha:
                    bk = safe_write.backup(fp)
                    say(f"{p} was edited after install — backed up to {bk} before removal")
                fp.unlink()
                removed.append(p)
            except OSError as e:
                errors.append(f"{p}: {e}")
        for b in prev.get("blocks") or []:
            try:
                res = placement.remove_foreign_block(b)
                if res["action"] in ("block-removed", "deleted-empty"):
                    blocks_removed.append(f"{b['file']} ({res['action']})")
            except (safe_write.SafeWriteError, OSError, KeyError) as e:
                errors.append(f"{b.get('file')}: {e}")
        for j in prev.get("json_keys") or []:
            try:
                res = safe_write.remove_json(j["file"], j["key"])
                if res["action"] == "key-removed":
                    keys_removed.append(f"{j['file']} :: {j['key']}")
            except (safe_write.SafeWriteError, OSError, ValueError, KeyError) as e:
                errors.append(f"{j.get('file')}: {e}")
        for d in sorted(prev.get("created_dirs") or [], key=len, reverse=True):
            try:
                if os.path.isdir(d) and not os.listdir(d):
                    os.rmdir(d)
            except OSError:
                pass
    else:
        project = Path(placement.ap(args.project_dir or os.getcwd()))
        say("no installed.json — removing a pre-2.0 install by its known paths (only files that are provably Coordn8's)")
        for p, kind in _legacy_targets(project):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if kind == "agents":
                if placement.BLOCK_BEGIN_RE.search(text):
                    res = safe_write.remove_block(p)
                    blocks_removed.append(f"{p} ({res['action']})")
                    continue
                if placement.BRAIN_BEGIN in text:
                    src = PKG_ROOT / "adapters" / "codex" / "AGENTS.md"
                    src_text = src.read_text(encoding="utf-8") if src.is_file() else text
                    if placement.classify(p, src_text, None) in ("ours-legacy", "identical"):
                        p.unlink()
                        removed.append(str(p))
                    else:
                        kept.append(f"{p} (has your own content as well as an old Coordn8 brain — left in place)")
                continue
            if placement.BRAIN_BEGIN in text or p.name in ("PLAYBOOK.md", "coordn8-brain.md"):
                p.unlink()
                removed.append(str(p))
            else:
                kept.append(f"{p} (does not look like a Coordn8 file — left in place)")
        sk = claude_home() / "skills" / "coordn8"
        if sk.is_dir() and not os.listdir(str(sk)):
            sk.rmdir()
    if prev:
        try:
            placement.installed_path().unlink()
        except OSError:
            pass
    for p in removed:
        say(f"removed {p}")
    for b in blocks_removed:
        say(f"removed the Coordn8 block from {b}")
    for k in keys_removed:
        say(f"removed config key {k}")
    for k in kept:
        say(f"kept {k}")
    for e in errors:
        warn(e)
    (say if mcp_un["status"] in ("ok", "skipped") else warn)(f"MCP servers: {mcp_un['detail']}")
    say(f"uninstalled: {len(removed)} package file(s), {len(blocks_removed)} block(s), {len(keys_removed)} "
        "config key(s). Your data in the user home was not touched.")

    if args.purge and args.yes:
        if h.exists():
            shutil.rmtree(str(h))
            say(f"purged {h}")
        else:
            say(f"{h} does not exist — nothing to purge")
    return 1 if errors else 0


# --------------------------------------------------------------------- status
def cmd_status(args) -> int:
    prev = placement.load_installed()
    if not prev:
        out = {"installed": False, "user_home": str(user_home())}
        print(json.dumps(out, indent=1) if args.json else f"[coordn8] not installed (no {placement.installed_path()})")
        return 0
    files = {}
    for p, sha in (prev.get("manifest") or {}).items():
        cur = placement.sha256_file(Path(p))
        files[p] = "ok" if cur == sha else ("missing" if cur is None else "modified")
    blocks = {b["file"]: ("present" if placement._has_block(Path(b["file"]), b.get("style", "html")) else "missing")
              for b in prev.get("blocks") or [] if b.get("file")}
    out = {"installed": True, "version": prev.get("version"), "commit": prev.get("commit"),
           "date": prev.get("date"), "package_version_here": placement.package_version(PKG_ROOT),
           "files": files, "blocks": blocks, "steps": prev.get("steps")}
    if args.json:
        print(json.dumps(out, indent=1))
        return 0
    bad = {p: s for p, s in files.items() if s != "ok"}
    say(f"installed v{out['version']} (commit {out['commit']}, {out['date']}); this package is "
        f"v{out['package_version_here']}")
    say(f"{len(files)} package file(s): {len(files) - len(bad)} intact" + (f", {len(bad)} changed/missing" if bad else ""))
    for p, s in bad.items():
        print(f"    {s}: {p}")
    for f, s in blocks.items():
        print(f"    block {s}: {f}")
    return 0


def _never_pause_on_the_terminal() -> None:
    """A terminal program that writes to or reads from the terminal while it is not the
    foreground job gets SIGTTOU/SIGTTIN, and the default action PAUSES it (state 'T'),
    which looks exactly like a hung install. Ignore both: writes still reach the screen,
    a background read fails fast instead of pausing, and the run carries on."""
    import signal
    for name in ("SIGTTOU", "SIGTTIN"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, signal.SIG_IGN)
            except (OSError, ValueError):
                pass


def main(argv=None) -> int:
    _never_pause_on_the_terminal()
    _ensure_utf8_stdout()
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd is None:
        ap.print_help()
        return 1
    try:
        if args.cmd in ("init", "update", "upgrade"):
            return cmd_init(args)
        if args.cmd == "uninstall":
            return cmd_uninstall(args)
        return cmd_status(args)
    except KeyboardInterrupt:
        warn("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
