#!/usr/bin/env node
/*
 * Coordn8 CLI — `npx coordn8 <command>`
 * Author: pandeom.  Node >= 14, zero npm dependencies.
 *
 *   npx coordn8 init [target]   install adapter(s): claude|cursor|codex|kiro|generic|all
 *   npx coordn8 update [target] force-refresh to the latest (clears the npx cache) and reinstall
 *   npx coordn8 uninstall       remove installed adapters
 *   npx coordn8 print           print the raw brain (paste into any agent)
 *   npx coordn8 setup           auto-install missing runtime tools (ffmpeg/chromium/tts) to unlock video
 *   npx coordn8 detect          run environment/tier detection
 *   npx coordn8 gates <folder>  run the advisory gate report on a build folder
 *   npx coordn8 orchestrate <f> supervised fail-early verification (parallel, adaptive, halts early)
 *   npx coordn8 check           verify adapters are in sync with PLAYBOOK.md
 *   npx coordn8 version
 */
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const VERSION = fs.readFileSync(path.join(ROOT, 'VERSION'), 'utf8').trim();

function py() {
  for (const c of ['python3', 'python']) {
    const r = spawnSync(c, ['--version'], { stdio: 'ignore' });
    if (r.status === 0) return c;
  }
  return null;
}

function runPy(rel, args) {
  const p = py();
  if (!p) {
    console.error('[coordn8] python3 not found — required for this command.');
    process.exit(2);
  }
  const r = spawnSync(p, [path.join(ROOT, rel), ...args], { stdio: 'inherit' });
  process.exit(r.status == null ? 1 : r.status);
}

// Run install.sh WITHOUT exiting the process, so callers can do follow-up work
// (e.g. print a next-steps note) and decide the exit code themselves. Returns
// the child's numeric status (1 if it couldn't be spawned at all).
function runShStatus(args) {
  const r = spawnSync('bash', [path.join(ROOT, 'install.sh'), ...args], {
    stdio: 'inherit',
  });
  if (r.error) {
    if (r.error.code === 'ENOENT') {
      console.error('[coordn8] could not find `bash` on PATH — it is required to run the installer.\n'
        + '          On Windows, run this from Git Bash or WSL.');
    } else {
      console.error('[coordn8] installer failed to start: ' + r.error.message);
    }
    return 1;
  }
  return r.status == null ? 1 : r.status;
}

function runSh(args) {
  process.exit(runShStatus(args));
}

const NPX_SPEC = 'github:ompandey98/coordn8';

// Best-effort clear of the npx / npm exec cache so the NEXT `npx github:…` call
// re-clones the repo instead of serving a stale cached copy. This is the fix for
// "it keeps running an old version". `npm cache clean --force` is non-interactive
// and safe — the cache is fully disposable. Never throws; returns true on success.
function clearNpxCache() {
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const clean = spawnSync(npm, ['cache', 'clean', '--force'], { stdio: 'ignore' });
  if (clean.error || clean.status !== 0) {
    console.error('[coordn8] note: could not clear the npm cache automatically. '
      + `If a later run still shows an old version, run:  npx --yes ${NPX_SPEC}@latest version`);
    return false;
  }
  return true;
}

// A short, consistent "what to do next" reminder printed after every successful
// install/update. The #1 support issue is a stale window not showing /coordn8 —
// so we always say it out loud.
function printNextSteps() {
  console.log('\n[coordn8] ✅ installed/updated to v' + VERSION + '.');
  console.log('[coordn8] IMPORTANT: open a NEW session (Claude Code reads its command list at start) —');
  console.log('[coordn8] a window that was already open won\'t show /coordn8 until you restart it.');
  console.log('[coordn8] Then, at your repo root, invoke:  /coordn8');
}

// `init`: install (and always OVERWRITE) the adapter(s). It also clears the npx
// cache up-front so this exact command, run again later, re-fetches the latest
// from GitHub and overrides the current install — i.e. it auto-updates every
// time with no extra flags. Errors are handled and reported, not dumped as a
// stack trace, and never abort silently.
function runInit(args) {
  clearNpxCache();  // best-effort; keeps future `npx …` runs fresh
  const status = runShStatus(args.length ? args : ['all']);
  if (status === 0) printNextSteps();
  else console.error('[coordn8] install did not complete cleanly (exit ' + status + '). See messages above.');
  process.exit(status);
}

// `update`/`upgrade`: same as init today (init already clears the cache and
// overwrites). Kept as a distinct verb for muscle memory / discoverability.
function runUpdate(args) {
  console.log('[coordn8] forcing a refresh to the latest and reinstalling …');
  runInit(args);  // runInit exits the process
}

function printBrain() {
  process.stdout.write(fs.readFileSync(path.join(ROOT, 'engine', 'PLAYBOOK.md'), 'utf8'));
}

function help() {
  console.log(`Coordn8 v${VERSION} — codebase to interactive page + narrated video (author: pandeom)

Usage (no clone needed — runs straight from GitHub; add -y so npx never prompts):
  npx -y github:ompandey98/coordn8 init [target]   Install (always overwrites w/ latest): claude|cursor|codex|kiro|generic|all
  npx -y github:ompandey98/coordn8 update [target] Force-refresh to the latest (clears the npx cache) + reinstall
  npx -y github:ompandey98/coordn8 uninstall              Remove installed adapters
  npx -y github:ompandey98/coordn8 print                 Print the raw engine brain (paste into any agent)
  npx -y github:ompandey98/coordn8 setup                 Auto-install missing tools (ffmpeg/chromium/TTS) to unlock the video — sudo-free where possible
  npx -y github:ompandey98/coordn8 detect                Detect environment capabilities, the tier, and the parallel budget
  npx -y github:ompandey98/coordn8 orchestrate <folder>  Supervised FAIL-EARLY verification: cheap source/plan/trace stages
                                                         run before anything expensive, in parallel on an adaptive pool that
                                                         ramps up on spare capacity and backs off under load
  npx -y github:ompandey98/coordn8 gates <folder>        Run every gate as a flat advisory report (no stage ordering)
  npx -y github:ompandey98/coordn8 check                 Verify adapters are byte-in-sync with PLAYBOOK.md
  npx -y github:ompandey98/coordn8 version               Print version

Then open a FRESH session at your repo root and invoke Coordn8.`);
}

const [cmd, ...rest] = process.argv.slice(2);
try {
switch (cmd) {
  case 'init':
    runInit(rest);
    break;
  case 'update':
  case 'upgrade':
    runUpdate(rest);
    break;
  case 'uninstall':
    runSh(['uninstall']);
    break;
  case 'print':
    printBrain();
    break;
  case 'setup':
    runPy('scripts/setup_env.py', rest);
    break;
  case 'detect':
    runPy('scripts/env_detect.py', rest);
    break;
  case 'gates':
    runPy('scripts/run_gates.py', rest);
    break;
  case 'orchestrate':
  case 'verify':
    // Supervised, fail-early, adaptively-parallel verification (PLAYBOOK R.3a/R.3b):
    // cheap source/plan/trace stages run BEFORE anything expensive, and a finding in
    // those stages halts the run so nothing is rendered on a known-bad input.
    runPy('scripts/orchestrate.py', rest);
    break;
  case 'check':
    runPy('build/generate_adapters.py', ['--check']);
    break;
  case 'version':
  case '--version':
  case '-v':
    console.log(VERSION);
    break;
  case undefined:
  case 'help':
  case '--help':
  case '-h':
    help();
    break;
  default:
    console.error(`[coordn8] unknown command: ${cmd}\n`);
    help();
    process.exit(1);
}
} catch (err) {
  // Never dump a raw stack trace at the user — report cleanly and exit non-zero.
  console.error('[coordn8] error: ' + (err && err.message ? err.message : String(err)));
  process.exit(1);
}
