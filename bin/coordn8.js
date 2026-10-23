#!/usr/bin/env node
/*
 * Coordn8 CLI — `npx coordn8 <command>`
 * Author: pandeom.  Node >= 14, zero npm dependencies.
 *
 *   npx coordn8 init [target]   install everything: adapter(s) claude|cursor|codex|kiro|generic|all,
 *                               then dependencies, voices, MCP servers, references, sign-in offers last
 *                               (--no-deps --no-voices --no-mcp --no-refs --no-signin --yes --project-dir D)
 *   npx coordn8 update [target] same as init (always force-updates to the latest)
 *   npx coordn8 uninstall       remove what Coordn8 installed; --purge --yes also deletes ~/.coordn8
 *   npx coordn8 status          what is installed and whether it is intact
 *   npx coordn8 print           print the raw brain (paste into any agent)
 *   npx coordn8 setup           auto-install missing runtime tools (ffmpeg/chromium/tts) to unlock video
 *   npx coordn8 detect          run environment/tier detection
 *   npx coordn8 gates <folder>  run the advisory gate report on a build folder
 *   npx coordn8 doctor          measure engine speed, frame size, projected time + disk
 *   npx coordn8 quality <fldr>  measured per-scene output quality (alignment/sync/overlap/...)
 *   npx coordn8 perf <folder>   judge a finished render's speed / host impact
 *   npx coordn8 disk <folder>   report intermediates + free space
 *   npx coordn8 clean <paths>   reclaim render intermediates
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


// Python-backed commands added in 2.0. Each maps to one script; a missing script
// reports itself plainly instead of crashing.
const PY_COMMANDS = {
  index: 'scripts/index/build_index.py',
  find: 'scripts/index/query.py',
  memory: 'scripts/memory/store.py',
  migrate: 'scripts/memory/migrate.py',
  palette: 'scripts/design/palette.py',
  diagram: 'scripts/design/pick_diagram.py',
  distill: 'scripts/design/distill.py',
  voices: 'scripts/voice/voices.py',
  mcp: 'scripts/mcp/install_mcp.py',
  refs: 'scripts/refs/refresh.py',
  reflect: 'scripts/reflect.py',
  bench: 'bench/run.py',
  evals: 'bench/evals/run_evals.py',
  brand: 'scripts/design/brand.py',
  cast: 'scripts/voice/cast.py',
  review: 'scripts/review/frame_review.py',
  recall: 'scripts/memory/recall.py',
  curate: 'scripts/memory/curate.py',
  federation: 'scripts/federation.py',
  explain: 'scripts/design/modes.py',
};

function runPyCommand(name, args) {
  // `refs check` reads the dated reference copies; plain `refs` refreshes them
  const rel = (name === 'refs' && args[0] === 'check')
    ? (args.shift(), 'scripts/refs/check.py') : PY_COMMANDS[name];
  if (!fs.existsSync(path.join(ROOT, rel))) {
    console.error(`[coordn8] '${name}' is not available in this build (missing ${rel}).`);
    process.exit(2);
  }
  runPy(rel, args);
}

const NPX_SPEC = 'github:ompandey98/coordn8';
const INSTALLER = 'scripts/install/installer.py';

// The python installer (scripts/install/installer.py) does the whole install: package files,
// dependencies, voices, MCP servers, references, sign-in offers last — each step time-bounded,
// never waiting on input, never overwriting a user file. It needs python >= 3.8.
function pyForInstaller() {
  for (const c of ['python3', 'python']) {
    const r = spawnSync(c, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'],
      { stdio: 'ignore' });
    if (!r.error && r.status === 0) return c;
  }
  return null;
}

// Returns the installer's exit status, or null when python is unavailable (caller falls back).
function runInstaller(sub, args) {
  const p = pyForInstaller();
  if (!p || !fs.existsSync(path.join(ROOT, INSTALLER))) return null;
  const r = spawnSync(p, [path.join(ROOT, INSTALLER), sub, ...args], { stdio: 'inherit' });
  if (r.error) {
    console.error('[coordn8] installer failed to start: ' + r.error.message);
    return 1;
  }
  return r.status == null ? 1 : r.status;
}

// Fallback force-update when python is missing: remove ONLY Coordn8's own cached copies from
// the npx cache (<cache>/_npx/<hash>/ holding node_modules/coordn8, or whose package.json
// depends on coordn8), so the NEXT `npx github:…` call re-fetches the newest commit. The rest
// of the npm cache — other packages, _cacache, logs — is never touched. (v1.5.0 ran
// `npm cache clean --force` here, which wiped the user's ENTIRE npm cache.) Never throws.
function npmCacheDir() {
  const env = process.env.npm_config_cache || process.env.NPM_CONFIG_CACHE;
  if (env) return env;
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
  const r = spawnSync(npm, ['config', 'get', 'cache'],
    { stdio: ['ignore', 'pipe', 'ignore'], timeout: 5000, encoding: 'utf8' });
  const out = (r.stdout || '').trim().split(/\r?\n/).pop();
  if (!r.error && r.status === 0 && out && out !== 'undefined' && out !== 'null') return out;
  return path.join(require('os').homedir(), '.npm');
}

function isCoordn8NpxEntry(dir) {
  if (fs.existsSync(path.join(dir, 'node_modules', 'coordn8'))) return true;
  try {
    const pj = JSON.parse(fs.readFileSync(path.join(dir, 'package.json'), 'utf8'));
    for (const field of ['dependencies', 'devDependencies', 'optionalDependencies']) {
      const deps = pj && pj[field];
      if (deps && typeof deps === 'object') {
        for (const name of Object.keys(deps)) {
          if (name === 'coordn8' || String(deps[name]).toLowerCase().indexOf('ompandey98/coordn8') !== -1) return true;
        }
      }
    }
  } catch (e) { /* no or unreadable package.json: not ours */ }
  return false;
}

function clearCoordn8NpxCache() {
  try {
    const npx = path.join(npmCacheDir(), '_npx');
    if (!fs.existsSync(npx) || fs.lstatSync(npx).isSymbolicLink()) return 0;
    let n = 0;
    for (const name of fs.readdirSync(npx)) {
      const dir = path.join(npx, name);
      const st = fs.lstatSync(dir);
      if (st.isSymbolicLink() || !st.isDirectory() || !isCoordn8NpxEntry(dir)) continue;
      if (fs.rmSync) fs.rmSync(dir, { recursive: true, force: true });
      else fs.rmdirSync(dir, { recursive: true });
      n += 1;
    }
    console.log(`[coordn8] force-update: removed ${n} cached Coordn8 cop${n === 1 ? 'y' : 'ies'} from ${npx} — nothing else touched`);
    return n;
  } catch (e) {
    console.error('[coordn8] note: could not clear Coordn8\'s cached copy automatically. '
      + `If a later run still shows an old version, run:  npx --yes ${NPX_SPEC}@latest version`);
    return 0;
  }
}

// A short, consistent "what to do next" reminder printed after every successful
// install/update. The #1 support issue is a stale window not showing /coordn8 —
// so we always say it out loud.
function printNextSteps() {
  console.log('\n[coordn8] ✅ installed/updated to v' + VERSION + '.');
  console.log('[coordn8] IMPORTANT: open a NEW session (Claude Code reads its command list at start) —');
  console.log('[coordn8] a window that was already open won\'t show /coordn8 until you restart it.');
  console.log('[coordn8] Then, at your repo root, invoke:  /coordn8');
  console.log('[coordn8] Check the install any time with:  npx -y ' + NPX_SPEC + ' status');
}

// `init`: install (and always OVERWRITE the package-owned files with) the latest. The python
// installer runs every step in order and, after a successful install, removes ONLY Coordn8's
// own cached copy from the npx cache, so this exact command run again later re-fetches the
// newest commit — it auto-updates every time with no extra flags. Without python, install.sh's
// bash fallback installs the adapters (block-merging any user file) and the targeted npx-cache
// cleanup runs here instead. Errors are reported, not dumped as a stack trace.
function runInit(args) {
  let status = runInstaller('init', args);
  if (status === null) {
    console.error('[coordn8] python3 (>= 3.8) not found — installing the adapters only via install.sh '
      + '(dependencies, voices and MCP servers need python3).');
    status = runShStatus(args.length ? args : ['all']);
    if (status === 0) clearCoordn8NpxCache();
  }
  if (status === 0) printNextSteps();
  else console.error('[coordn8] install did not complete cleanly (exit ' + status + '). See messages above.');
  process.exit(status);
}

// `update`/`upgrade`: same as init (init always force-updates and overwrites package files).
// Kept as a distinct verb for muscle memory / discoverability.
function runUpdate(args) {
  console.log('[coordn8] forcing a refresh to the latest and reinstalling …');
  runInit(args);  // runInit exits the process
}

// `uninstall [--purge --yes]`: removes the package-owned files and the Coordn8 blocks/keys it
// inserted (per ~/.coordn8/installed.json), nothing else. --purge lists ~/.coordn8 and deletes
// it only with --yes.
function runUninstall(args) {
  let status = runInstaller('uninstall', args);
  if (status === null) {
    if (args.indexOf('--purge') !== -1) {
      console.error('[coordn8] --purge needs python3 (it lists what it would delete before deleting); nothing deleted.');
      process.exit(2);
    }
    status = runShStatus(['uninstall']);
  }
  process.exit(status);
}

function runStatus(args) {
  const status = runInstaller('status', args);
  if (status === null) {
    console.error('[coordn8] status needs python3.');
    process.exit(2);
  }
  process.exit(status);
}

function printBrain() {
  process.stdout.write(fs.readFileSync(path.join(ROOT, 'engine', 'PLAYBOOK.md'), 'utf8'));
}

function help() {
  console.log(`Coordn8 v${VERSION} — codebase to interactive page + narrated video (author: pandeom)

Usage (no clone needed — runs straight from GitHub; add -y so npx never prompts):
  npx -y github:ompandey98/coordn8 init [target]   Install EVERYTHING, always the latest: claude|cursor|codex|kiro|generic|all
                                                   then dependencies, voices, MCP servers, references; sign-in offers
                                                   last (20 s, default skip). Never waits on input, never overwrites a
                                                   file of yours (backs it up and merges a Coordn8 block instead).
                                                   Options: --no-deps --no-voices --no-mcp --no-refs --no-signin --yes
                                                   --project-dir DIR
  npx -y github:ompandey98/coordn8 update [target] Same as init (force-refreshes only Coordn8's cached copy)
  npx -y github:ompandey98/coordn8 uninstall             Remove what Coordn8 installed (your data in ~/.coordn8 is kept;
                                                         add --purge --yes to delete it — it lists everything first)
  npx -y github:ompandey98/coordn8 status                What is installed (version, commit) and whether it is intact
  npx -y github:ompandey98/coordn8 print                 Print the raw engine brain (paste into any agent)
  npx -y github:ompandey98/coordn8 setup                 Auto-install missing tools (ffmpeg/chromium/TTS) to unlock the video — sudo-free where possible
  npx -y github:ompandey98/coordn8 detect                Detect environment capabilities, the tier, and the parallel budget
  npx -y github:ompandey98/coordn8 orchestrate <folder>  Supervised FAIL-EARLY verification: cheap source/plan/trace stages
                                                         run before anything expensive, in parallel on an adaptive pool that
                                                         ramps up on spare capacity and backs off under load
  npx -y github:ompandey98/coordn8 gates <folder>        Run every gate as a flat advisory report (no stage ordering)
  npx -y github:ompandey98/coordn8 doctor --probe        PREFLIGHT: measure this machine — render engine, real
                                                         frames/s, measured frame size — then project the time and
                                                         disk a job will need (add --duration/--fps/--scenes)
  npx -y github:ompandey98/coordn8 quality <folder>      MEASURED output quality per scene x dimension, in parallel:
                                                         alignment, overlap, voice-sync DRIFT (ms), density,
                                                         legibility, motion — scored, with the weakest named
                                                         (add --prev <build> to see regressions vs the last cut)
  npx -y github:ompandey98/coordn8 perf <folder>         Judge a finished render: too slow, or too hard on the host?
                                                         Both are failures; it names which one happened
  npx -y github:ompandey98/coordn8 disk <folder>         What intermediate data a build holds + free space
  npx -y github:ompandey98/coordn8 clean <paths>         Reclaim render intermediates (try --dry-run first)
  npx -y github:ompandey98/coordn8 check                 Verify adapters are byte-in-sync with PLAYBOOK.md
  npx -y github:ompandey98/coordn8 version               Print version

Tools (each prints its own --help):
  index <repo> · find <query>      Retrieval index over a project (path:line hits); per-folder maps
  memory · recall · curate         Two-level memory: list/supersede entries; session recall; capture a preference
  palette · diagram · distill · brand   Palette director, diagram-type selector, style distillation, brand profiles
  voices [list|install|test] · cast <brief>   Local voices; cast the best one for a narrative
  review prepare|collect <build>   Automatic frame review (contact sheets for the blind reviewer)
  reflect record|wrap|retry-prompt Failure analysis and retry-with-the-record
  refs · refs check                Refresh / check the dated public references
  mcp [list|install|status] · federation match|connectors · explain   MCP servers; other skills; explain modes
  bench · evals                    Benchmark fixtures; skill-vs-baseline evals

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
    runUninstall(rest);
    break;
  case 'status':
    runStatus(rest);
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
  // Disk safety. A render must never be able to fill the user's volume, and an
  // abandoned run must never keep costing them space.
  case 'clean':
    runPy('scripts/render/capture_scene.py', ['clean', ...(rest.length ? rest : ['.'])]);
    break;
  case 'disk':
    runPy('scripts/gates/check_disk.py', rest.length ? rest : ['.']);
    break;
  // Observability. `doctor` MEASURES this machine before you commit an hour to a
  // render; `perf` judges a finished run's health from the telemetry it wrote.
  case 'doctor':
    runPy('scripts/doctor.py', rest);
    break;
  case 'perf':
    runPy('scripts/gates/check_perf.py', rest.length ? rest : ['.']);
    break;
  // Output-QUALITY observability: measured per scene, per dimension, in parallel.
  case 'quality':
    runPy('scripts/quality.py', rest.length ? rest : ['.']);
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
    if (Object.prototype.hasOwnProperty.call(PY_COMMANDS, cmd)) {
      runPyCommand(cmd, rest);
      break;
    }
    console.error(`[coordn8] unknown command: ${cmd}\n`);
    help();
    process.exit(1);
}
} catch (err) {
  // Never dump a raw stack trace at the user — report cleanly and exit non-zero.
  console.error('[coordn8] error: ' + (err && err.message ? err.message : String(err)));
  process.exit(1);
}
