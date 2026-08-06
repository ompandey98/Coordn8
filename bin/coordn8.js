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

function runSh(args) {
  const r = spawnSync('bash', [path.join(ROOT, 'install.sh'), ...args], {
    stdio: 'inherit',
  });
  process.exit(r.status == null ? 1 : r.status);
}

// `update`: the fix for "it keeps running an old version". When Coordn8 is
// launched via `npx github:ompandey98/coordn8`, npx caches the cloned repo and
// will happily keep serving a STALE copy — so a plain re-run never picks up new
// commits. This clears that cache (non-interactively) so the NEXT `npx …` call
// re-fetches the latest, then reinstalls the adapters from whatever brain is
// present locally right now. Never prompts; safe to run unattended.
function runUpdate(args) {
  const spec = 'github:ompandey98/coordn8';
  // 1) Best-effort clear of the npx / npm exec cache so the next npx re-clones.
  //    `npm cache clean --force` is non-interactive and safe (cache is disposable).
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const clean = spawnSync(npm, ['cache', 'clean', '--force'], { stdio: 'inherit' });
  if (clean.status !== 0) {
    console.error('[coordn8] note: could not clear the npm cache automatically. '
      + `If you still see an old version, run:  npx --yes ${spec}@latest version`);
  } else {
    console.log('[coordn8] cleared the npx/npm cache — the next `npx ' + spec
      + '` will fetch the latest.');
  }
  // 2) Reinstall adapters from the brain in THIS checkout (regen happens in install.sh).
  console.log('[coordn8] reinstalling adapters from the current brain …');
  runSh(args.length ? args : ['all']);  // runSh exits the process
}

function printBrain() {
  process.stdout.write(fs.readFileSync(path.join(ROOT, 'engine', 'PLAYBOOK.md'), 'utf8'));
}

function help() {
  console.log(`Coordn8 v${VERSION} — codebase to interactive page + narrated video (author: pandeom)

Usage (no clone needed — runs straight from GitHub):
  npx github:ompandey98/coordn8 init [target]   Install: claude|cursor|codex|kiro|generic|all
  npx github:ompandey98/coordn8 update [target] Force-refresh to the latest (clears the npx cache) + reinstall
  npx github:ompandey98/coordn8 uninstall       Remove installed adapters
  npx github:ompandey98/coordn8 print           Print the raw engine brain (paste into any agent)
  npx github:ompandey98/coordn8 setup           Auto-install missing tools (ffmpeg/chromium/TTS) to unlock the video — sudo-free where possible
  npx github:ompandey98/coordn8 detect          Detect environment capabilities and the tier
  npx github:ompandey98/coordn8 gates <folder>  Run the advisory gate report on a build folder
  npx github:ompandey98/coordn8 check           Verify adapters are byte-in-sync with PLAYBOOK.md
  npx github:ompandey98/coordn8 version         Print version

Then open a FRESH session at your repo root and invoke Coordn8.`);
}

const [cmd, ...rest] = process.argv.slice(2);
switch (cmd) {
  case 'init':
    runSh(rest.length ? rest : ['all']);
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
