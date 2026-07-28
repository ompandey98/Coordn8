#!/usr/bin/env node
/*
 * Coordn8 CLI — `npx coordn8 <command>`
 * Author: pandeom.  Node >= 14, zero npm dependencies.
 *
 *   npx coordn8 init [target]   install adapter(s): claude|cursor|codex|kiro|generic|all
 *   npx coordn8 uninstall       remove installed adapters
 *   npx coordn8 print           print the raw brain (paste into any agent)
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

function printBrain() {
  process.stdout.write(fs.readFileSync(path.join(ROOT, 'engine', 'PLAYBOOK.md'), 'utf8'));
}

function help() {
  console.log(`Coordn8 v${VERSION} — codebase to interactive page + narrated video (author: pandeom)

Usage (no clone needed — runs straight from GitHub):
  npx github:ompandey98/coordn8 init [target]   Install: claude|cursor|codex|kiro|generic|all
  npx github:ompandey98/coordn8 uninstall       Remove installed adapters
  npx github:ompandey98/coordn8 print           Print the raw engine brain (paste into any agent)
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
  case 'uninstall':
    runSh(['uninstall']);
    break;
  case 'print':
    printBrain();
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
