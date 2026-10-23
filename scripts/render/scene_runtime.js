/*
 * Coordn8 — SCENE RUNTIME (the package-level fix for the whole D1/D2/D3/D4/D5 family)
 * Author: pandeom.  Zero dependencies. Runs in a browser (canvas) AND in Node (trace-only).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * A production build shipped ten real defects with every automated gate green. Six of the
 * ten came from ONE cause: scenes addressed narration beats BY INDEX (`beat(3)`) and the
 * frame-lock helpers CLAMPED an out-of-range index instead of raising. So every edit to the
 * narration line count — the single most common operation in the workflow — silently
 * mistimed some beat. Clamping turned six loud failures into six silent ones.
 *
 * This runtime removes those defect classes at the SOURCE rather than detecting them later:
 *
 *   #1  NAMED BEATS THAT THROW.  `beat('the-worked-example')` resolves against the script.
 *       An unknown name or an out-of-range index THROWS — loudly, with the valid set listed.
 *       Deleting a narration line becomes one obvious resolve error instead of six silent
 *       mistimings. (Requirements addendum Part 8 #1 — highest-value change in the document.)
 *   #2  INSTRUMENTATION MODE.  Every draw records {kind,x,y,w,h,alpha} boxes, connector
 *       polylines {from,to,points,arrowAt} and asset draws {path,crop,subjectId,scale}. Every
 *       semantic gate depends on this; without it they cannot be written at all.
 *   #3  SECTIONS.  `section('name', fn)` declares a named region, enabling per-section
 *       presence (S7) and previous-cut regression diff (S8) — "this whole section is empty".
 *   #4  BOUNDED TEXT BY DEFAULT.  Text drawn inside a container is width-fitted to that
 *       container. Overflowing requires an explicit `unbounded: true` opt-in. Kills the
 *       child-escape defect class rather than reporting it.
 *   #5  SUBJECT IDS.  Every asset draw and every beat declares `subjectId`; a mismatch is a
 *       gate failure (S5) — the "right shape, wrong content" defect nothing else can catch.
 *   #6  CONNECTOR from/to.  Every connector declares its endpoints by node id, so direction
 *       (S6) becomes checkable — no geometry gate has any concept of direction.
 *   + NON-FINITE ALPHA IS FATAL.  A NaN alpha draws NOTHING, silently. In verify mode any
 *       non-finite alpha/coordinate THROWS at the draw call (S1 at the source, ~20 lines).
 *   #7  THE ACTIVE KIT.  Colours, type sizes, radius and motion default to window.__C8_KIT__
 *       (scripts/design/kit_export.py); the resolved colours are recorded in the trace.
 *   #8  WORDS THAT THROW.  atLine(name) / atWord(name, word) read window.__C8_WORDS__ (the
 *       aligner's output) and throw when the line or the word is not there.
 *   #9  DETERMINISM.  C8.random(seed) is the only randomness (recorded in the trace); while a
 *       frame is drawn, Math.random / Date.now / new Date() / performance.now THROW.
 *       scripts/gates/check_determinism.py checks the same rule statically and by re-tracing.
 *
 * TWO EXECUTION MODES
 * -------------------
 *   Browser: include this file, then `renderFrame(t)` paints the canvas. With
 *            `window.__INSTRUMENT = true` it also records the trace for that frame.
 *   Node:    `node scene_runtime.js trace <scene.js> --out trace.json` executes the scene's
 *            draw function over a t-grid with a NO-OP canvas and writes the trace. This is
 *            what lets every semantic gate run with NO browser installed — the gates read the
 *            trace, not pixels. (Pixel-level checks — density, Stage V — still need a render.)
 */
'use strict';

(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') { window.Coordn8 = api; if (!window.C8) window.C8 = api; }
})(this, function () {

  // ---------------------------------------------------------------------------
  // Verify mode: ON by default everywhere except an explicit production render.
  // The whole point is that a mistake is LOUD at authoring time, so the default
  // must be strict. `Coordn8.setVerify(false)` is the deliberate opt-out.
  // ---------------------------------------------------------------------------
  // The 1080p legibility floor. Text is never auto-shrunk below this: an
  // unreadable caption is a worse defect than a truncated one.
  const MIN_LEGIBLE_PX = 28;

  let VERIFY = true;
  let INSTRUMENT = false;

  function setVerify(v) { VERIFY = !!v; }
  function setInstrument(v) { INSTRUMENT = !!v; }

  class SceneError extends Error {
    constructor(msg) { super('[coordn8:scene] ' + msg); this.name = 'SceneError'; }
  }

  function fail(msg) { throw new SceneError(msg); }

  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }

  const G = (typeof globalThis !== 'undefined') ? globalThis
    : (typeof window !== 'undefined' ? window : {});

  // ---------------------------------------------------------------------------
  // ACTIVE STYLE KIT (A3). The same kit the page reads as --c8-* CSS vars, exported by
  // scripts/design/kit_export.py as `window.__C8_KIT__` (or passed to setKit(), or per
  // scene via `new Scene({kit})` / `buildTrace({kit})`). It is looked up LAZILY, so a kit
  // script loaded after this file still applies. Every default falls back to the previous
  // hard-coded value, so a scene with no kit renders exactly as before; switching kits is
  // a data change, never a code edit. The resolved colours are RECORDED in the trace, so
  // "the runtime read the kit" is checkable without a browser.
  // ---------------------------------------------------------------------------
  let KIT_SET = false, KIT_OVERRIDE = null;
  function setKit(k) {
    if (k === undefined) { KIT_SET = false; KIT_OVERRIDE = null; return; }
    KIT_SET = true; KIT_OVERRIDE = k || null;
  }
  function globalKit() {
    if (KIT_SET) return KIT_OVERRIDE;
    return (G.window && G.window.__C8_KIT__) || G.__C8_KIT__ || null;
  }
  function lookup(obj, path, fallback) {
    let v = obj;
    for (const p of String(path).split('.')) { if (v == null) return fallback; v = v[p]; }
    return v == null ? fallback : v;
  }
  function kitValue(path, fallback) { return lookup(globalKit(), path, fallback); }

  // ---------------------------------------------------------------------------
  // WORD TIMINGS (E2, runtime half). `window.__C8_WORDS__` is written by the aligner
  // (scripts/render/align.py) in this shape:
  //   {"lines":[{"name","text","start","end","words":[{"w","start","end","i"}]}],
  //    "source":"engine|onset"}
  // atLine(name) / atWord(name, word, occurrence=1) resolve against it and THROW — with the
  // valid set listed — when the line or the word is not there. A narration edit that drops
  // the word a visual was landing on becomes one loud error instead of a silent mistiming
  // (the same reasoning as named beats, one level finer). The timing returned is the
  // aligner's OWN measurement; nothing is estimated from character positions here.
  // ---------------------------------------------------------------------------
  let WORDS_SET = false, WORDS_OVERRIDE = null;
  const WORDS_CACHE = (typeof WeakMap !== 'undefined') ? new WeakMap() : null;

  function normWord(s) {
    return String(s == null ? '' : s)
      .replace(/[⟦⟧]/g, '')                 // caption marks ⟦ ⟧ never count
      .toLowerCase()
      .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '');
  }

  function indexWords(raw) {
    if (raw == null) return null;
    if (WORDS_CACHE && typeof raw === 'object' && WORDS_CACHE.has(raw)) return WORDS_CACHE.get(raw);
    if (typeof raw !== 'object' || !Array.isArray(raw.lines)) {
      fail('word timings must be {"lines":[{name,text,start,end,words:[{w,start,end,i}]}], "source"} ' +
           '— the aligner (scripts/render/align.py) writes this shape');
    }
    const byName = new Map();
    raw.lines.forEach((ln, li) => {
      if (!ln || typeof ln.name !== 'string' || !ln.name) {
        fail(`word-timing line ${li} has no name — atLine()/atWord() address lines BY NAME`);
      }
      if (byName.has(ln.name)) fail(`duplicate line name '${ln.name}' in the word timings`);
      if (!finite(ln.start) || !finite(ln.end) || ln.end < ln.start) {
        fail(`word-timing line '${ln.name}' has invalid start/end (${ln.start}, ${ln.end})`);
      }
      const words = Array.isArray(ln.words) ? ln.words : [];
      const norm = words.map((w, wi) => {
        if (!w || !finite(w.start) || !finite(w.end) || w.end < w.start) {
          fail(`word ${wi} of line '${ln.name}' has non-finite or reversed timing ` +
               `(${w && w.start}, ${w && w.end}) — a NaN word time is the D1 class one level down`);
        }
        if (w.start < ln.start - 0.25 || w.end > ln.end + 0.25) {
          fail(`word '${w.w}' (${w.start}–${w.end}s) lies outside its line '${ln.name}' ` +
               `(${ln.start}–${ln.end}s) — the alignment is corrupt for this line`);
        }
        return { w: String(w.w), key: normWord(w.w), start: w.start, end: w.end,
                 i: Number.isInteger(w.i) ? w.i : wi };
      });
      byName.set(ln.name, { name: ln.name, text: String(ln.text || ''), start: ln.start,
                            end: ln.end, words: norm });
    });
    const idx = { source: raw.source || null, byName, names: Array.from(byName.keys()) };
    if (WORDS_CACHE && typeof raw === 'object') WORDS_CACHE.set(raw, idx);
    return idx;
  }

  function setWords(w) {
    if (w === undefined) { WORDS_SET = false; WORDS_OVERRIDE = null; return; }
    WORDS_SET = true; WORDS_OVERRIDE = w || null;
    if (w) indexWords(w);                               // validate NOW, not at first use
  }
  function globalWords() {
    if (WORDS_SET) return WORDS_OVERRIDE;
    return (G.window && G.window.__C8_WORDS__) || G.__C8_WORDS__ || null;
  }

  function resolveLine(idx, name, who) {
    if (!idx) {
      fail(`${who}('${name}'): no word timings are loaded. Load the aligner's output as ` +
           `window.__C8_WORDS__ (or setWords()/buildTrace({words})) before addressing words.`);
    }
    if (typeof name !== 'string' || !idx.byName.has(name)) {
      fail(`${who}: unknown line '${name}'. Lines in the word timings: ` +
           `[${idx.names.join(', ') || 'none'}]. A renamed or deleted narration line must be ` +
           `updated in the scene too — this error exists so that edit cannot mistime it silently.`);
    }
    return idx.byName.get(name);
  }

  function lineView(ln) {
    return { name: ln.name, text: ln.text, start: ln.start, end: ln.end,
             words: ln.words.map(w => ({ w: w.w, start: w.start, end: w.end, i: w.i })) };
  }

  function findWord(ln, word, occurrence, who) {
    const occ = occurrence === undefined ? 1 : occurrence;
    if (!Number.isInteger(occ) || occ < 1) fail(`${who}: occurrence must be an integer ≥ 1 (got ${occ})`);
    const parts = String(word == null ? '' : word).split(/\s+/).map(normWord).filter(Boolean);
    if (!parts.length) fail(`${who}('${ln.name}', ${JSON.stringify(word)}): empty word`);
    const hits = [];
    for (let i = 0; i + parts.length <= ln.words.length; i++) {
      let ok = true;
      for (let k = 0; k < parts.length; k++) if (ln.words[i + k].key !== parts[k]) { ok = false; break; }
      if (ok) hits.push(i);
    }
    if (!hits.length) {
      const inText = ln.text && normWord(ln.text).split(/[^\p{L}\p{N}]+/u).join(' ').includes(parts.join(' '));
      const have = ln.words.map(w => w.w).join(' ');
      fail(`${who}('${ln.name}', '${word}'): the word is NOT in that line` +
           (inText ? ' (it appears in the line text but the aligner produced no timing for it)' : '') +
           `. Words with timings: [${have.slice(0, 160)}${have.length > 160 ? '…' : ''}]. ` +
           `A narration edit removed or changed it — re-anchor the visual on a word that is ` +
           `actually spoken, or the frame will land on nothing.`);
    }
    if (occ > hits.length) {
      fail(`${who}('${ln.name}', '${word}', ${occ}): only ${hits.length} occurrence(s) in that line`);
    }
    const i0 = hits[occ - 1], a = ln.words[i0], b = ln.words[i0 + parts.length - 1];
    return { w: parts.length === 1 ? a.w : ln.words.slice(i0, i0 + parts.length).map(x => x.w).join(' '),
             start: a.start, end: b.end, i: a.i, line: ln.name, occurrence: occ };
  }

  /** The aligned line (name, text, start, end, words). THROWS if the line is unknown. */
  function atLine(name, words) {
    return lineView(resolveLine(indexWords(words === undefined ? globalWords() : words), name, 'atLine'));
  }

  /**
   * The aligned timing of `word` (or a multi-word phrase) in line `name`:
   * {w, start, end, i, line, occurrence}. THROWS if the line or the word is not there.
   */
  function atWord(name, word, occurrence, words) {
    const ln = resolveLine(indexWords(words === undefined ? globalWords() : words), name, 'atWord');
    return findWord(ln, word, occurrence, 'atWord');
  }

  // ---------------------------------------------------------------------------
  // SEEDED RANDOMNESS (E10/H3). C8.random(seed) is the ONLY randomness a scene may use:
  // mulberry32 over a 32-bit seed (strings are FNV-1a hashed). The same seed gives the
  // same sequence on every frame, every machine, every run; every seed used is recorded in
  // the trace with how many draws it served, so a build's randomness is reproducible and
  // auditable. A missing or non-finite seed THROWS — "random" without a seed is exactly the
  // nondeterminism this exists to remove.
  // ---------------------------------------------------------------------------
  const RNG_LOG = new Map();

  function seed32(seed) {
    if (typeof seed === 'string') {
      let h = 0x811c9dc5;
      for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 0x01000193); }
      return h >>> 0;
    }
    if (!finite(seed)) fail(`random(seed) needs a finite number or a string seed (got ${seed})`);
    return (Math.floor(seed) >>> 0);
  }

  function random(seed, label) {
    if (seed === undefined || seed === null) {
      fail('random() needs a seed — unseeded randomness makes a frame depend on the run, not on t');
    }
    let a = seed32(seed);
    const key = `${typeof seed === 'string' ? JSON.stringify(seed) : seed}|${label || ''}`;
    if (!RNG_LOG.has(key)) RNG_LOG.set(key, { seed, seed32: a, label: label || null, maxDraws: 0 });
    const rec = RNG_LOG.get(key);
    let draws = 0;
    const next = function () {
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      draws += 1;
      if (draws > rec.maxDraws) rec.maxDraws = draws;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    next.seed = seed;
    next.range = (lo, hi) => lo + (hi - lo) * next();
    next.int = (lo, hi) => lo + Math.floor(next() * (hi - lo + 1));
    next.pick = (arr) => {
      if (!Array.isArray(arr) || !arr.length) fail('random.pick() needs a non-empty array');
      return arr[Math.floor(next() * arr.length)];
    };
    return next;
  }

  // ---------------------------------------------------------------------------
  // CLOCK GUARD (E10). While a frame is being drawn (Scene.begin .. Scene.end, and every
  // buildTrace sample), Math.random, Date.now, `new Date()`/`Date()` and performance.now
  // THROW. A frame must be a pure function of t: a clock read or an unseeded random makes
  // the same t render differently on a re-render, which is how a crash-safe parallel render
  // silently produces a jittering video. Every attempt is also LOGGED (so a scene that
  // swallows the throw in a try/catch is still caught by the trace). `setClockGuard(false)`
  // is the deliberate opt-out and is recorded in the trace.
  // ---------------------------------------------------------------------------
  let GUARD_ENABLED = true;
  let GUARD_OWNER = null;                   // the Scene whose frame is open
  let GUARD_SAVED = null;
  let CURRENT = null;                       // {name, t, log} of the frame being drawn
  const VIOLATIONS = [];

  function setClockGuard(v) { GUARD_ENABLED = !!v; }

  function clockViolation(call) {
    const where = CURRENT ? ` at t=${CURRENT.t.toFixed(3)}s in scene '${CURRENT.name}'` : '';
    const rec = { call, t: CURRENT ? CURRENT.t : null, scene: CURRENT ? CURRENT.name : null };
    VIOLATIONS.push(rec);
    if (CURRENT && CURRENT.log) CURRENT.log.push(rec);
    throw new SceneError(
      `nondeterministic call to ${call} during a frame draw${where}. A frame must be a pure ` +
      `function of t: use C8.random(seed) for randomness and t / named beats / atWord() for ` +
      `time. CSS animations, timers and requestAnimationFrame timestamps are banned in scenes ` +
      `for the same reason.`);
  }

  // One frame is open at a time. A scene that called begin() and never end() (a crashed
  // draw, an authoring slip) hands the guard to the next scene's begin(), and that
  // scene's end() releases it — the guard can never be left installed after a trace.
  function acquireGuard(owner) {
    if (!GUARD_ENABLED) return false;
    if (GUARD_OWNER && GUARD_OWNER !== owner) { GUARD_OWNER._guarded = false; GUARD_OWNER = owner; return true; }
    if (!GUARD_OWNER) { installGuard(); GUARD_OWNER = owner; }
    return true;
  }
  function releaseGuard(owner) {
    if (GUARD_OWNER !== owner) return;
    GUARD_OWNER = null;
    removeGuard();
  }

  function installGuard() {
    if (GUARD_SAVED) return;
    const perf = G.performance || null;
    GUARD_SAVED = {
      random: Math.random, dateNow: Date.now, Date: G.Date, perf,
      perfOwn: perf ? Object.prototype.hasOwnProperty.call(perf, 'now') : false,
      perfNow: perf ? perf.now : null,
    };
    const RealDate = GUARD_SAVED.Date;
    Math.random = function () { return clockViolation('Math.random()'); };
    RealDate.now = function () { return clockViolation('Date.now()'); };
    try {
      G.Date = new Proxy(RealDate, {
        construct(target, args, nt) {
          if (!args.length) return clockViolation('new Date()');
          return Reflect.construct(target, args, nt === G.Date ? target : nt);
        },
        apply() { return clockViolation('Date()'); },
      });
    } catch (e) { /* a host without Proxy keeps Date.now trapped at least */ }
    if (perf) { try { perf.now = function () { return clockViolation('performance.now()'); }; } catch (e) { /* frozen */ } }
  }

  function removeGuard() {
    const s = GUARD_SAVED; GUARD_SAVED = null;
    if (!s) return;
    Math.random = s.random;
    s.Date.now = s.dateNow;
    G.Date = s.Date;
    if (s.perf) {
      try { if (s.perfOwn) s.perf.now = s.perfNow; else delete s.perf.now; } catch (e) { /* frozen */ }
    }
  }

  function num(v, fb) { return finite(v) ? v : fb; }

  // ---------------------------------------------------------------------------
  // SCRIPT / BEATS — named, resolved, and THROWING (package change #1)
  // ---------------------------------------------------------------------------
  //
  // A script is the ordered list of spoken lines for ONE scene. Each line may carry
  // a stable `name` (author-chosen, e.g. 'worked-example') and a `subjectId` (what
  // instance/case this line is about — see #5). Timings come from the TTS speech
  // marks upstream; the runtime never invents them.
  class Script {
    constructor(lines) {
      if (!Array.isArray(lines) || lines.length === 0) {
        fail('Script requires a non-empty array of lines');
      }
      this.lines = lines.map((ln, i) => {
        if (!finite(ln.start) || !finite(ln.end)) {
          fail(`line ${i} (${ln.name || ln.text || '?'}) has non-finite start/end ` +
               `(start=${ln.start}, end=${ln.end}) — timings must come from the speech marks`);
        }
        if (ln.end < ln.start) fail(`line ${i} ends before it starts`);
        return {
          idx: i,
          name: ln.name || null,
          text: ln.text || '',
          speech: ln.speech || null,   // PLAYBOOK 2.2c spoken form (TTS only)
          subjectId: ln.subjectId || ln.subject_id || null,
          start: ln.start,
          end: ln.end,
        };
      });
      // Duplicate names would make beat('x') ambiguous — that is an authoring bug.
      const seen = new Map();
      for (const ln of this.lines) {
        if (!ln.name) continue;
        if (seen.has(ln.name)) {
          fail(`duplicate beat name '${ln.name}' (lines ${seen.get(ln.name)} and ${ln.idx}) ` +
               `— beat names must be unique so beat('${ln.name}') is unambiguous`);
        }
        seen.set(ln.name, ln.idx);
      }
      this.byName = seen;
      this.duration = this.lines[this.lines.length - 1].end;
    }

    get length() { return this.lines.length; }

    names() { return this.lines.map(l => l.name).filter(Boolean); }

    /**
     * Resolve a beat reference to its line. THE CORE FIX: never clamps.
     * Accepts a name ('worked-example') or an index (3). Anything unresolvable
     * throws with the full valid set, so the error names its own fix.
     */
    beat(ref) {
      if (typeof ref === 'string') {
        if (!this.byName.has(ref)) {
          fail(`unknown beat name '${ref}'. Valid names: [${this.names().join(', ') || 'none declared'}]. ` +
               `A renamed or deleted narration line must be updated here too — ` +
               `this error exists so that edit cannot silently mistime the scene.`);
        }
        return this.lines[this.byName.get(ref)];
      }
      if (typeof ref === 'number') {
        if (!Number.isInteger(ref)) fail(`beat index ${ref} is not an integer`);
        if (ref < 0 || ref >= this.lines.length) {
          fail(`beat index ${ref} is OUT OF RANGE for this scene (valid 0..${this.lines.length - 1}, ` +
               `${this.lines.length} lines). This is the clamp-bug class: the old runtime would have ` +
               `silently clamped to the last line and mistimed the beat. Prefer beat('name').`);
        }
        return this.lines[ref];
      }
      fail(`beat() needs a name or an index, got ${typeof ref}`);
    }

    /** Boundary array: [l0.start, l1.start, …, lN.end] — valid indices 0..length. */
    bounds() {
      const b = this.lines.map(l => l.start);
      b.push(this.lines[this.lines.length - 1].end);
      return b;
    }

    /** Safe boundary access — the raw `B[i]` defect (S2/D1) made impossible. */
    boundary(i) {
      const b = this.bounds();
      if (!Number.isInteger(i) || i < 0 || i >= b.length) {
        fail(`boundary index ${i} out of range (valid 0..${b.length - 1}). ` +
             `Raw B[i] past the array is what produced a NaN opacity and an entirely blank section.`);
      }
      return b[i];
    }

    /** Which line (if any) is being spoken at t. Null during deliberate silence. */
    speakingAt(t) {
      for (const ln of this.lines) if (t >= ln.start && t <= ln.end) return ln;
      return null;
    }

    /**
     * Build the script straight from the aligner's word timings, so line names and
     * timings have ONE source. `subjects` optionally maps line name -> subjectId.
     */
    static fromWords(words, subjects) {
      const idx = indexWords(words);
      if (!idx || !idx.names.length) fail('Script.fromWords needs word timings with at least one line');
      const subj = subjects || {};
      return new Script(idx.names.map((n) => {
        const ln = idx.byName.get(n);
        return { name: ln.name, text: ln.text, start: ln.start, end: ln.end, subjectId: subj[ln.name] || null };
      }));
    }
  }

  // ---------------------------------------------------------------------------
  // EASING / TIMING — pure functions of t (crash-safe render contract)
  // ---------------------------------------------------------------------------
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function easeOutCubic(x) { return 1 - Math.pow(1 - clamp01(x), 3); }

  // ---------------------------------------------------------------------------
  // THE SCENE — draw surface + instrumentation recorder
  // ---------------------------------------------------------------------------
  class Scene {
    /**
     * @param {object} opts {name, script, ctx (CanvasRenderingContext2D|null), width, height}
     */
    constructor(opts) {
      opts = opts || {};
      if (!opts.name) fail('a scene needs a name');
      this.name = opts.name;
      this.script = opts.script instanceof Script ? opts.script : new Script(opts.script || []);
      this.ctx = opts.ctx || null;             // null => trace-only (Node) mode
      this.width = opts.width || 1920;
      this.height = opts.height || 1080;
      // Which subsystem/system this SCENE is about (C8). An asset belonging to a
      // different subsystem must not appear here.
      this.subsystem = opts.subsystem || opts.system || null;
      // The asset manifest this scene PROMISES to show (C2). A declared asset that
      // never actually draws is the "the screenshot we prepared never appeared"
      // defect — invisible unless the promise is written down.
      this.declaredAssets = Array.isArray(opts.assets) ? opts.assets.slice() : [];
      // Per-scene kit / word timings; `undefined` means "use the active global one".
      this._kit = opts.kit;
      this._words = opts.words;
      if (this._words) indexWords(this._words);          // validate at construction
      this._forms = new Map();                 // catalog/invented scene forms used (H1/H5)
      this._violations = [];                   // clock-guard hits, even if swallowed
      this._guarded = false;
      this.t = 0;
      this._boxes = [];
      this._connectors = [];
      this._assets = [];
      this._cursors = [];
      this._nodes = new Map();                 // id -> {x,y,w,h} for connector checking
      this._sectionStack = [];
      this._containerStack = [];
      this._sectionsSeen = new Map();          // name -> {drawCount, inkBoxes}
    }

    // ---- frame lifecycle --------------------------------------------------
    begin(t) {
      if (!finite(t)) fail(`renderFrame called with non-finite t=${t}`);
      this.t = t;
      this._boxes = [];
      this._connectors = [];
      this._assets = [];
      this._cursors = [];
      this._sectionStack = [];
      this._containerStack = [];
      this._sectionsSeen = new Map();
      // The frame is now being drawn: clock reads and unseeded randomness THROW until end().
      this._guarded = acquireGuard(this);
      CURRENT = { name: this.name, t, log: this._violations };
      if (this.ctx) { this.ctx.save(); this.ctx.clearRect(0, 0, this.width, this.height); }
      return this;
    }

    end() {
      this._release();
      if (this.ctx) this.ctx.restore();
      return this.frame();
    }

    _release() {
      if (this._guarded) { releaseGuard(this); this._guarded = false; }
      if (CURRENT && CURRENT.log === this._violations) CURRENT = null;
    }

    /**
     * Draw one frame safely: begin(t) → draw(this, t) → end(), with the clock guard
     * released even when the draw throws. This is the entry point a browser
     * `renderAt(t)` hook should call.
     */
    render(t, draw) {
      if (typeof draw !== 'function') fail('render(t, draw) needs a draw(scene, t) function');
      this.begin(t);
      try { draw(this, t); } finally { this._release(); if (this.ctx) this.ctx.restore(); }
      return this.frame();
    }

    // ---- active kit (A3) ---------------------------------------------------
    /** The kit this scene reads: its own (opts.kit) or the active global one. */
    get kit() { return this._kit !== undefined ? this._kit : globalKit(); }

    /** A value from the active kit by dotted path, or `fallback`. */
    kitValue(path, fallback) { return lookup(this.kit, path, fallback); }

    /** The kit's motion curve (sampled lookup table), else ease-out-cubic. */
    ease(x) {
      const s = this.kitValue('motion.ease_samples', null);
      const u = clamp01(x);
      if (!Array.isArray(s) || s.length < 2) return easeOutCubic(u);
      const f = u * (s.length - 1), i = Math.floor(f);
      if (i >= s.length - 1) return num(s[s.length - 1], 1);
      const a = num(s[i], 0), b = num(s[i + 1], 1);
      return clamp01(a + (b - a) * (f - i));
    }

    /** The kit's stagger between grouped entrances, in seconds. */
    stagger() { return num(this.kitValue('motion.stagger_s', 0.08), 0.08); }

    // ---- seeded randomness (E10) -------------------------------------------
    /** C8.random(seed) bound to this scene: same seed → same sequence, every frame. */
    random(seed, label) { return random(seed, label); }

    // ---- word timings (E2, runtime half) -----------------------------------
    _wordIndex() { return indexWords(this._words !== undefined ? this._words : globalWords()); }
    atLine(name) { return lineView(resolveLine(this._wordIndex(), name, 'atLine')); }
    atWord(name, word, occurrence) {
      return findWord(resolveLine(this._wordIndex(), name, 'atWord'), word, occurrence, 'atWord');
    }

    /** Entrance alpha keyed to the moment a WORD is spoken (throws if it is not there). */
    wordAlpha(line, word, opts) {
      const o = opts || {};
      const w = this.atWord(line, word, o.occurrence);
      const fadeIn = finite(o.fadeIn) ? o.fadeIn : num(this.kitValue('motion.fadeIn', 0.35), 0.35);
      const lead = finite(o.lead) ? o.lead : num(this.kitValue('motion.lead', 0.12), 0.12);
      let a = this.ease((this.t - (w.start - lead)) / Math.max(1e-6, fadeIn));
      if (o.until !== undefined) {
        const outStart = typeof o.until === 'string' ? this.beat(o.until).start
          : this.atWord(o.until.line, o.until.word, o.until.occurrence).start;
        const fadeOut = finite(o.fadeOut) ? o.fadeOut : num(this.kitValue('motion.fadeOut', 0.3), 0.3);
        a = Math.min(a, 1 - this.ease((this.t - outStart) / Math.max(1e-6, fadeOut)));
      }
      return clamp01(a);
    }

    /** Progress 0..1 from the moment a word is spoken, over `dur` seconds (default: the word). */
    wordProgress(line, word, opts) {
      const o = opts || {};
      const w = this.atWord(line, word, o.occurrence);
      const lead = finite(o.lead) ? o.lead : 0;
      const dur = finite(o.dur) ? o.dur : Math.max(0.05, w.end - w.start);
      if (dur <= 0) return this.t >= w.start - lead ? 1 : 0;
      return clamp01((this.t - (w.start - lead)) / dur);
    }

    // ---- scene forms (H1/H5) -----------------------------------------------
    /**
     * Record which scene form a section uses: a catalog entry ({catalog:'funnel'}) or an
     * INVENTED one ({invented:'queue-lane', reason:'…'}). Recorded only — no gate ever
     * fails a form for not being in the catalog (freedom doctrine).
     */
    declareForm(spec) {
      const s = spec || {};
      const name = s.invented || s.catalog;
      if (!name) fail("declareForm needs {catalog:'<id>'} or {invented:'<name>', reason:'…'}");
      if (s.invented && !s.reason) fail(`invented scene form '${s.invented}' needs a reason`);
      const key = `${s.invented ? 'invented' : 'catalog'}:${name}:${s.section || this._currentSection() || ''}`;
      if (!this._forms.has(key)) {
        this._forms.set(key, { kind: s.invented ? 'invented' : 'catalog', name,
                               section: s.section || this._currentSection() || null,
                               reason: s.reason || null, overBudget: s.overBudget || null });
      }
      return this;
    }

    /** The recorded trace for the current frame. */
    frame() {
      const spoken = this.script.speakingAt(this.t);
      return {
        t: round3(this.t),
        speaking: spoken ? { idx: spoken.idx, name: spoken.name, subjectId: spoken.subjectId } : null,
        boxes: this._boxes,
        connectors: this._connectors,
        assets: this._assets,
        cursors: this._cursors,
        sections: Array.from(this._sectionsSeen.entries())
          .map(([name, v]) => ({ name, drawCount: v.drawCount, inkArea: Math.round(v.inkArea) })),
      };
    }

    // ---- beat helpers (all throwing) --------------------------------------
    beat(ref) { return this.script.beat(ref); }

    /** Progress 0..1 through a beat, with an optional lead-in (never past 1). */
    beatProgress(ref, opts) {
      const ln = this.beat(ref);
      const o = opts || {};
      const lead = finite(o.lead) ? o.lead : 0;          // start easing in slightly early
      const dur = finite(o.dur) ? o.dur : (ln.end - ln.start);
      const t0 = ln.start - lead;
      if (dur <= 0) return this.t >= t0 ? 1 : 0;
      return clamp01((this.t - t0) / dur);
    }

    /**
     * Standard entrance alpha for an element owned by a beat. This is THE helper
     * scenes should use instead of hand-rolling a timing expression — it cannot
     * produce NaN, and it resolves the beat by name.
     */
    beatAlpha(ref, opts) {
      const o = opts || {};
      const ln = this.beat(ref);
      // Motion signature comes from the ACTIVE KIT when one is loaded (A3): its entrance
      // duration, its anticipation lead and its easing curve. No kit => the previous values.
      const fadeIn = finite(o.fadeIn) ? o.fadeIn : num(this.kitValue('motion.fadeIn', 0.35), 0.35);
      const lead = finite(o.lead) ? o.lead : num(this.kitValue('motion.lead', 0.12), 0.12);  // ~120ms anticipation (PLAYBOOK 2.3)
      const holdUntil = o.until === undefined ? null : o.until;
      const start = ln.start - lead;
      let a = this.ease((this.t - start) / Math.max(1e-6, fadeIn));
      if (holdUntil !== null) {
        const out = this.beat(holdUntil);                // also throws if unknown
        const fadeOut = finite(o.fadeOut) ? o.fadeOut : num(this.kitValue('motion.fadeOut', 0.3), 0.3);
        const outA = 1 - this.ease((this.t - out.start) / Math.max(1e-6, fadeOut));
        a = Math.min(a, outA);
      }
      return clamp01(a);
    }

    // ---- sections (package change #3) ------------------------------------
    /**
     * Declare a named section. S7 asserts every declared section actually draws
     * content of its own; S8 diffs a section's ink against the last approved cut.
     */
    section(name, fn) {
      if (!name || typeof name !== 'string') fail('section() needs a name');
      if (typeof fn !== 'function') fail(`section('${name}') needs a draw function`);
      if (!this._sectionsSeen.has(name)) this._sectionsSeen.set(name, { drawCount: 0, inkArea: 0 });
      this._sectionStack.push(name);
      try { fn(this); } finally { this._sectionStack.pop(); }
      return this;
    }

    _currentSection() {
      return this._sectionStack.length ? this._sectionStack[this._sectionStack.length - 1] : null;
    }

    // ---- the instrumented record ------------------------------------------
    _record(box) {
      // S1 AT THE SOURCE: a non-finite alpha draws NOTHING, silently. Never allow it.
      if (!finite(box.alpha)) {
        fail(`non-finite alpha (${box.alpha}) on a ${box.kind} draw at t=${this.t.toFixed(2)}s` +
             (box.text ? ` text="${String(box.text).slice(0, 40)}"` : '') +
             `. A NaN alpha renders nothing with no error — this is the defect that left an entire ` +
             `section blank while the narration described it. Check the timing expression's inputs.`);
      }
      for (const k of ['x', 'y', 'w', 'h']) {
        if (!finite(box[k])) {
          fail(`non-finite ${k} (${box[k]}) on a ${box.kind} draw at t=${this.t.toFixed(2)}s`);
        }
      }
      box.section = this._currentSection();
      box.parent = this._containerStack.length
        ? this._containerStack[this._containerStack.length - 1].id : null;
      if (INSTRUMENT || !this.ctx) this._boxes.push(box);
      const sec = box.section && this._sectionsSeen.get(box.section);
      if (sec && box.alpha > 0.18) {
        sec.drawCount += 1;
        sec.inkArea += Math.max(0, box.w) * Math.max(0, box.h) * box.alpha;
      }
      return box;
    }

    // ---- primitives -------------------------------------------------------
    /** A content container (card/panel). Children drawn in `fn` are BOUNDED to it (#4). */
    card(spec, fn) {
      const s = spec || {};
      const box = this._record({
        kind: s.kind || 'card', id: s.id || null,
        x: s.x, y: s.y, w: s.w, h: s.h, alpha: s.alpha,
        pad: finite(s.pad) ? s.pad : num(this.kitValue('card.pad', 18), 18),
        fill: s.fill || this.kitValue('card.fill', 'rgba(255,255,255,0.06)'),
        stroke: s.stroke || this.kitValue('card.stroke', null),
        radius: finite(s.radius) ? s.radius : num(this.kitValue('card.radius', 14), 14),
      });
      if (s.id) this._nodes.set(s.id, { x: s.x, y: s.y, w: s.w, h: s.h });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = box.fill;
        if (c.roundRect) { c.beginPath(); c.roundRect(box.x, box.y, box.w, box.h, box.radius); c.fill(); }
        else c.fillRect(box.x, box.y, box.w, box.h);
        if (box.stroke) {
          c.strokeStyle = box.stroke; c.lineWidth = num(this.kitValue('card.stroke_width', 1), 1);
          if (c.roundRect) { c.beginPath(); c.roundRect(box.x, box.y, box.w, box.h, box.radius); c.stroke(); }
          else c.strokeRect(box.x, box.y, box.w, box.h);
        }
        c.restore();
      }
      if (typeof fn === 'function') {
        this._containerStack.push({ id: s.id || `card@${s.x},${s.y}`, box });
        try { fn(this); } finally { this._containerStack.pop(); }
      }
      return box;
    }

    /**
     * Text. BOUNDED BY DEFAULT (#4): inside a container, the string is width-fitted
     * to the container's inner width. `unbounded: true` is the explicit opt-in that
     * allows overflow — which is exactly what the child-escape defect needed.
     */
    text(spec) {
      const s = spec || {};
      const size = finite(s.size) ? s.size : Math.max(MIN_LEGIBLE_PX, num(this.kitValue('text.sizes.body', 28), 28));
      const str = String(s.text === undefined ? '' : s.text);
      const parent = this._containerStack.length
        ? this._containerStack[this._containerStack.length - 1] : null;
      // Children INHERIT their container's alpha when they don't set one. Without
      // this, a card fading in leaves its own label at full opacity (a visible
      // seam), or the author hand-repeats the timing expression in every child —
      // which is exactly how a NaN alpha got copied into six places.
      if (s.alpha === undefined && parent) s.alpha = parent.box.alpha;

      // Measure: real ctx when available, else a deterministic estimate so Node
      // trace mode produces the same *shape* of finding.
      const tw = s.weight || 500, tf = s.font || undefined;
      let w = this._measure(str, size, tw, tf);
      const h = Math.ceil(size * 1.25);
      let x = finite(s.x) ? s.x : (parent ? parent.box.x + parent.box.pad : 0);
      const y = finite(s.y) ? s.y : (parent ? parent.box.y + parent.box.pad : 0);
      let fitted = false, truncated = false, display = str;

      if (parent && !s.unbounded) {
        const inner = parent.box.w - 2 * parent.box.pad;
        if (inner <= 0) {
          fail(`card ${parent.id} has no inner width (w=${parent.box.w}, pad=${parent.box.pad}) ` +
               `— cannot bound text inside it`);
        }
        if (w > inner) {
          // Shrink to fit — but NEVER below the legibility floor. Fitting by shrinking
          // into illegibility just trades one defect (overflow) for a worse one
          // (unreadable text): a 19px caption at 1080p cannot be read, so truncating
          // is strictly better than shrinking past the floor. Below it we ellipsize.
          const minSize = finite(s.minSize)
            ? s.minSize
            : Math.min(size, MIN_LEGIBLE_PX);
          let trySize = size;
          while (trySize > minSize && this._measure(str, trySize, tw, tf) > inner) trySize -= 1;
          if (this._measure(str, trySize, tw, tf) > inner) {
            display = this._ellipsize(str, trySize, inner, tw, tf);
            truncated = true;
          }
          fitted = true;
          w = Math.min(inner, this._measure(display, trySize, tw, tf));
          spec = Object.assign({}, s, { size: trySize });
        }
        // Keep it inside horizontally no matter what.
        const left = parent.box.x + parent.box.pad;
        const right = parent.box.x + parent.box.w - parent.box.pad;
        if (s.align === 'center') x = left + Math.max(0, (right - left - w) / 2);
        else if (x < left) x = left;
        else if (x + w > right) x = Math.max(left, right - w);
      }

      const box = this._record({
        kind: 'text', x, y, w, h, alpha: s.alpha,
        text: display, size: (spec && spec.size) || size,
        fitted, truncated, unbounded: !!s.unbounded,
        color: s.color || this.kitValue('text.color', '#f2f4f8'),
        font: s.font || this.kitValue('text.font', 'Inter, system-ui, sans-serif'),
        weight: s.weight || 500,
      });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = box.color;
        c.font = `${box.weight} ${box.size}px ${box.font}`;
        c.textBaseline = 'top';
        c.fillText(display, box.x, box.y);
        c.restore();
      }
      return box;
    }

    /**
     * Width of `str` at `size`, measured with the SAME weight and family it will be drawn
     * with (a bold or wide kit face measured as regular under-fits its container).
     */
    _measure(str, size, weight, font) {
      const wt = weight || 500;
      if (this.ctx) {
        const c = this.ctx;
        c.save();
        c.font = `${wt} ${size}px ${font || this.kitValue('text.font', 'Inter, system-ui, sans-serif')}`;
        const m = c.measureText(str).width;
        c.restore();
        if (finite(m) && m > 0) return m;
      }
      // Deterministic fallback (~0.54em average advance for UI sans at these sizes; bold
      // faces run wider).
      return str.length * size * (Number(wt) >= 600 ? 0.57 : 0.54);
    }

    /** Public measure (what components use to lay text out before drawing it). */
    measureText(str, size, weight, font) { return this._measure(String(str), size, weight, font); }

    /** The width a chip with this label will take (same weight/family the chip draws with). */
    chipWidth(label, size, font, padX) {
      const wt = this.kitValue('chip.weight', 600);
      return this._measure(String(label), size, wt, font) + 2 * (finite(padX) ? padX : 14);
    }

    _ellipsize(str, size, maxW, weight, font) {
      let s = str;
      while (s.length > 1 && this._measure(s + '…', size, weight, font) > maxW) s = s.slice(0, -1);
      return s + '…';
    }

    /** A small labelled pill/chip. Bounded like text. */
    chip(spec) {
      const s = spec || {};
      // Same alpha inheritance as text() — a child never has to restate the
      // container's timing expression.
      if (s.alpha === undefined && this._containerStack.length) {
        s.alpha = this._containerStack[this._containerStack.length - 1].box.alpha;
      }
      // No kit => the historical 20px default; a kit's label size is never under 28px.
      const size = finite(s.size) ? s.size : num(this.kitValue('text.sizes.label', 20), 20);
      const label = String(s.text === undefined ? '' : s.text);
      const padX = finite(s.padX) ? s.padX : 14;
      const cweight = s.weight || this.kitValue('chip.weight', 600);
      const w = finite(s.w) ? s.w : this._measure(label, size, cweight, s.font) + padX * 2;
      const h = finite(s.h) ? s.h : Math.ceil(size * 1.9);
      const box = this._record({
        kind: 'chip', x: s.x, y: s.y, w, h, alpha: s.alpha, text: label, size,
        fill: s.fill || this.kitValue('chip.fill', 'rgba(120,170,255,0.16)'),
        color: s.color || this.kitValue('chip.color', '#dce8ff'),
        font: s.font || this.kitValue('text.font', 'Inter, system-ui, sans-serif'),
      });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = box.fill;
        if (c.roundRect) { c.beginPath(); c.roundRect(box.x, box.y, w, h, h / 2); c.fill(); }
        else c.fillRect(box.x, box.y, w, h);
        c.fillStyle = box.color;
        c.font = `${cweight} ${size}px ${box.font}`;
        c.textBaseline = 'middle';
        c.fillText(label, box.x + padX, box.y + h / 2);
        c.restore();
      }
      return box;
    }

    /** Register a diagram node so connectors can be direction-checked (#6). */
    node(spec) {
      const s = spec || {};
      if (!s.id) fail('node() needs an id so connectors can reference it');
      const box = this._record({
        kind: s.kind || 'node', id: s.id, x: s.x, y: s.y, w: s.w, h: s.h,
        alpha: s.alpha, text: s.text || null,
        fill: s.fill || this.kitValue('node.fill', 'rgba(255,255,255,0.10)'),
        stroke: s.stroke || this.kitValue('node.stroke', null),
        radius: finite(s.radius) ? s.radius : num(this.kitValue('node.radius', 12), 12),
      });
      this._nodes.set(s.id, { x: s.x, y: s.y, w: s.w, h: s.h });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = box.fill;
        if (c.roundRect) { c.beginPath(); c.roundRect(s.x, s.y, s.w, s.h, box.radius); c.fill(); }
        else c.fillRect(s.x, s.y, s.w, s.h);
        if (box.stroke) {
          c.strokeStyle = box.stroke; c.lineWidth = num(this.kitValue('node.stroke_width', 1.5), 1.5);
          if (c.roundRect) { c.beginPath(); c.roundRect(s.x, s.y, s.w, s.h, box.radius); c.stroke(); }
          else c.strokeRect(s.x, s.y, s.w, s.h);
        }
        c.restore();
      }
      if (s.text) {
        this._containerStack.push({ id: s.id, box: Object.assign({}, box, { pad: 12 }) });
        // Default label size is the 1080p LEGIBILITY FLOOR (28px), not something
        // smaller: every scene that draws a node inherits this default, so a value
        // under the floor would put every diagram below the readable threshold at
        // once. The quality scorecard caught exactly that.
        const labelSize = finite(s.textSize) ? s.textSize
          : Math.max(MIN_LEGIBLE_PX, num(this.kitValue('node.text_size', 30), 30));
        try { this.text({ text: s.text, alpha: box.alpha, align: 'center', color: s.color,
                          size: labelSize, y: s.y + s.h / 2 - labelSize / 2 });
        } finally { this._containerStack.pop(); }
      }
      return box;
    }

    /**
     * A connector between two DECLARED nodes (#6). `from`/`to` are node ids, so the
     * direction gate (S6) can check the drawn path against the declared intent and
     * that the arrowhead sits at the `to` end. No geometry gate can do this without
     * the declaration — which is why it is required, not optional.
     */
    connector(spec) {
      const s = spec || {};
      if (!s.from || !s.to) {
        fail(`connector() requires from/to node ids (got from=${s.from}, to=${s.to}). ` +
             `Direction defects — a path animating opposite to its own arrowhead — are ` +
             `undetectable without them.`);
      }
      const a = this._nodes.get(s.from);
      const b = this._nodes.get(s.to);
      if (!a) fail(`connector from unknown node '${s.from}' — declare it with node({id:'${s.from}',…}) first`);
      if (!b) fail(`connector to unknown node '${s.to}' — declare it with node({id:'${s.to}',…}) first`);
      const alpha = s.alpha;
      if (!finite(alpha)) fail(`connector ${s.from}->${s.to} has non-finite alpha (${alpha})`);

      // Default routing is EDGE-to-EDGE, not centre-to-centre: a line drawn through
      // the interior of its own endpoint boxes crosses those nodes' labels, which both
      // looks wrong and would (correctly) trip the text-on-connector gate.
      const edges = s.points && s.points.length ? null : edgePoints(a, b);
      const points = s.points && s.points.length ? s.points : [edges.from, edges.to];
      for (const p of points) {
        if (!finite(p.x) || !finite(p.y)) fail(`connector ${s.from}->${s.to} has a non-finite point`);
      }
      const rec = {
        kind: 'connector', from: s.from, to: s.to, points,
        arrowAt: s.arrowAt || 'to', alpha,
        section: this._currentSection(),
        color: s.color || this.kitValue('connector.color', 'rgba(160,200,255,0.75)'),
        width: finite(s.width) ? s.width : num(this.kitValue('connector.width', 3), 3),
        dash: Array.isArray(s.dash) ? s.dash : this.kitValue('connector.dash', []),
      };
      if (INSTRUMENT || !this.ctx) this._connectors.push(rec);
      if (this.ctx && alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = alpha;
        c.strokeStyle = rec.color;
        c.lineWidth = rec.width;
        if (c.setLineDash && Array.isArray(rec.dash)) c.setLineDash(rec.dash);
        c.beginPath(); c.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i++) c.lineTo(points[i].x, points[i].y);
        c.stroke();
        // Arrowhead at the declared end, so picture and declaration cannot disagree.
        const tip = rec.arrowAt === 'to' ? points[points.length - 1] : points[0];
        const prev = rec.arrowAt === 'to' ? points[points.length - 2] : points[1];
        if (c.setLineDash) c.setLineDash([]);
        if (prev) drawArrow(c, prev, tip, rec.width);
        c.restore();
      }
      return rec;
    }

    /**
     * An asset (screenshot/image) draw. `subjectId` is REQUIRED (#5): the beat says
     * what it is about, the asset says what it shows, and S5 fails on a mismatch —
     * the "real, legible, well-cropped, and about the wrong thing" defect.
     */
    asset(spec) {
      const s = spec || {};
      if (!s.path) fail('asset() needs a path');
      if (!s.subjectId && !s.subject_id) {
        fail(`asset('${s.path}') must declare subjectId — which instance/case it shows. ` +
             `Without it, a panel showing the evidence for a DIFFERENT example than the one ` +
             `being narrated is undetectable (it is real, legible and correctly composed).`);
      }
      // EFFECTIVE RENDER SCALE (C7 legibility). On-screen glyph size is governed
      // by the crop's WIDTH, not its height — cropping a text block SHORTER does
      // not enlarge its glyphs. So when the source width is declared we compute
      // the true scale rather than trusting a hand-passed number.
      const srcW = finite(s.srcW) ? s.srcW : (s.crop && finite(s.crop.w) ? s.crop.w : null);
      const scale = srcW && finite(s.w) && srcW > 0
        ? s.w / srcW
        : (finite(s.scale) ? s.scale : 1);
      const box = this._record({
        kind: 'shot', x: s.x, y: s.y, w: s.w, h: s.h, alpha: s.alpha,
        path: s.path, subjectId: s.subjectId || s.subject_id, scale,
      });
      const rec = {
        path: s.path, subjectId: box.subjectId, crop: s.crop || null,
        scale, srcW: srcW, alpha: box.alpha, x: s.x, y: s.y, w: s.w, h: s.h,
        section: this._currentSection(), caption: s.caption || null,
        // Which subsystem/system this asset BELONGS to (C8). Showing one system's
        // product while narrating another is factually wrong and no geometry gate
        // can see it — it needs a declaration.
        subsystem: s.subsystem || s.system || null,
      };
      if (INSTRUMENT || !this.ctx) this._assets.push(rec);
      if (this.ctx && box.alpha > 0.001 && s.image) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.drawImage(s.image, s.x, s.y, s.w, s.h);
        c.restore();
      }
      return box;
    }

    /**
     * A simulated cursor/pointer, used when walking a screenshot. Recorded so the
     * cursor-region gate (C3) can prove it sits ON the panel it is pointing at
     * during its spoken line, rather than parked in dead space — which reads as a
     * mistake to a viewer and is invisible to every geometry check.
     */
    cursor(spec) {
      const s = spec || {};
      if (!finite(s.x) || !finite(s.y)) {
        fail(`cursor() needs finite x/y (got ${s.x}, ${s.y})`);
      }
      const rec = { x: s.x, y: s.y, onAsset: s.onAsset || s.on || null,
                    alpha: finite(s.alpha) ? s.alpha : 1,
                    section: this._currentSection() };
      if (INSTRUMENT || !this.ctx) this._cursors.push(rec);
      if (this.ctx && rec.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = rec.alpha;
        c.fillStyle = s.color || '#ffffff';
        c.beginPath();
        c.moveTo(s.x, s.y);
        c.lineTo(s.x + 13, s.y + 20);
        c.lineTo(s.x + 5, s.y + 20);
        c.lineTo(s.x + 2, s.y + 29);
        c.closePath(); c.fill();
        c.restore();
      }
      return rec;
    }

    /**
     * A drawn MARK — the non-text visual vocabulary scenes need (bars, arcs, rings,
     * ticks, highlight outlines, a travelling token, trapezoids). Instrumented like every
     * other primitive: recorded with its bounding box, alpha (fatal if non-finite), section
     * and parent, so density and presence gates see it. `meaning` is REQUIRED: a mark that
     * explains nothing is decoration, and decoration with no referent is banned (0.4a).
     *
     *   shape 'rect'  {x,y,w,h,radius}         'dot'   {cx,cy,r}
     *         'line'  {points:[{x,y},…]}       'poly'  {points} (closed, filled)
     *         'arc'   {cx,cy,r,a0,a1,width}    'cross'/'check' {x,y,w,h}
     * `alpha` is visibility; `fillOpacity` is the data value (stepped ink) — never confused.
     */
    mark(spec) {
      const s = spec || {};
      const shape = s.shape || 'rect';
      if (!s.meaning || typeof s.meaning !== 'string') {
        fail(`mark('${shape}') needs a 'meaning' — what this visual explains. A mark with no ` +
             `referent is decoration, and decoration is banned (0.4a doctrine 2).`);
      }
      let bb;
      const pts = Array.isArray(s.points) ? s.points : null;
      if (shape === 'line' || shape === 'poly') {
        if (!pts || pts.length < 2) fail(`mark('${shape}') needs at least two points`);
        for (const p of pts) if (!finite(p.x) || !finite(p.y)) fail(`mark('${shape}') has a non-finite point`);
        const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
        const x0 = Math.min(...xs), y0 = Math.min(...ys);
        bb = { x: x0, y: y0, w: Math.max(1, Math.max(...xs) - x0), h: Math.max(1, Math.max(...ys) - y0) };
      } else if (shape === 'dot') {
        if (!finite(s.cx) || !finite(s.cy) || !finite(s.r)) fail('mark(dot) needs finite cx, cy, r');
        bb = { x: s.cx - s.r, y: s.cy - s.r, w: 2 * s.r, h: 2 * s.r };
      } else if (shape === 'arc') {
        for (const k of ['cx', 'cy', 'r', 'a0', 'a1']) if (!finite(s[k])) fail(`mark(arc) needs a finite ${k}`);
        const n = 24, xs = [], ys = [];
        for (let i = 0; i <= n; i++) {
          const a = s.a0 + (s.a1 - s.a0) * i / n;
          xs.push(s.cx + s.r * Math.cos(a)); ys.push(s.cy + s.r * Math.sin(a));
        }
        const pad = (finite(s.width) ? s.width : 4) / 2;
        const x0 = Math.min(...xs) - pad, y0 = Math.min(...ys) - pad;
        bb = { x: x0, y: y0, w: Math.max(...xs) + pad - x0, h: Math.max(...ys) + pad - y0 };
      } else {
        bb = { x: s.x, y: s.y, w: s.w, h: s.h };
      }
      const box = this._record({
        kind: 'mark', shape, meaning: s.meaning, id: s.id || null,
        x: bb.x, y: bb.y, w: bb.w, h: bb.h, alpha: s.alpha,
        fill: s.fill === undefined ? null : s.fill,
        stroke: s.stroke === undefined ? null : s.stroke,
        width: finite(s.width) ? s.width : null,
        fillOpacity: finite(s.fillOpacity) ? clamp01(s.fillOpacity) : null,
        dash: Array.isArray(s.dash) ? s.dash : null,
        glow: !!s.glow,
      });
      if (pts) box.points = pts.map(p => ({ x: round3(p.x), y: round3(p.y) }));
      if (shape === 'arc') Object.assign(box, { cx: s.cx, cy: s.cy, r: s.r, a0: round3(s.a0), a1: round3(s.a1) });
      if (shape === 'dot') Object.assign(box, { cx: s.cx, cy: s.cy, r: s.r });
      if (this.ctx && box.alpha > 0.001) this._paintMark(box, s);
      return box;
    }

    _paintMark(box, s) {
      const c = this.ctx;
      c.save(); c.globalAlpha = box.alpha;
      if (box.glow && box.stroke) { c.shadowColor = box.stroke; c.shadowBlur = 14; }
      if (c.setLineDash) c.setLineDash(box.dash || []);
      c.lineWidth = box.width || 3;
      c.lineCap = 'round';
      const fillIt = () => {
        if (!box.fill) return;
        c.save(); if (box.fillOpacity !== null) c.globalAlpha = box.alpha * box.fillOpacity;
        c.fillStyle = box.fill; c.fill(); c.restore();
      };
      const strokeIt = () => { if (box.stroke) { c.strokeStyle = box.stroke; c.stroke(); } };
      c.beginPath();
      if (box.shape === 'line' || box.shape === 'poly') {
        c.moveTo(box.points[0].x, box.points[0].y);
        for (let i = 1; i < box.points.length; i++) c.lineTo(box.points[i].x, box.points[i].y);
        if (box.shape === 'poly') { c.closePath(); fillIt(); }
        strokeIt();
      } else if (box.shape === 'dot') {
        c.arc(box.cx, box.cy, box.r, 0, Math.PI * 2); fillIt(); strokeIt();
      } else if (box.shape === 'arc') {
        c.arc(s.cx, s.cy, s.r, s.a0, s.a1); strokeIt();
      } else if (box.shape === 'cross' || box.shape === 'check') {
        if (box.shape === 'cross') {
          c.moveTo(box.x, box.y); c.lineTo(box.x + box.w, box.y + box.h);
          c.moveTo(box.x + box.w, box.y); c.lineTo(box.x, box.y + box.h);
        } else {
          c.moveTo(box.x, box.y + box.h * 0.55); c.lineTo(box.x + box.w * 0.38, box.y + box.h);
          c.lineTo(box.x + box.w, box.y);
        }
        strokeIt();
      } else {
        const r = finite(s.radius) ? s.radius : 0;
        if (c.roundRect && r) c.roundRect(box.x, box.y, box.w, box.h, r); else c.rect(box.x, box.y, box.w, box.h);
        fillIt(); strokeIt();
      }
      c.restore();
    }
  }

  function center(b) { return { x: b.x + b.w / 2, y: b.y + b.h / 2 }; }

  /**
   * Where a straight A->B line meets each box's EDGE. Keeps the connector outside
   * both endpoint boxes, so it never crosses their own labels.
   */
  function edgePoints(a, b) {
    const ca = center(a), cb = center(b);
    return { from: boxEdge(a, ca, cb), to: boxEdge(b, cb, ca) };
  }

  function boxEdge(box, from, toward) {
    const dx = toward.x - from.x, dy = toward.y - from.y;
    if (dx === 0 && dy === 0) return { x: from.x, y: from.y };
    const hw = box.w / 2, hh = box.h / 2;
    // Scale the ray until it hits the box boundary (the smaller of the two ratios).
    const sx = dx === 0 ? Infinity : hw / Math.abs(dx);
    const sy = dy === 0 ? Infinity : hh / Math.abs(dy);
    const s = Math.min(sx, sy);
    return { x: from.x + dx * s, y: from.y + dy * s };
  }
  function round3(n) { return Math.round(n * 1000) / 1000; }

  function drawArrow(c, from, to, lw) {
    const ang = Math.atan2(to.y - from.y, to.x - from.x);
    const len = 9 + lw * 2;
    c.beginPath();
    c.moveTo(to.x, to.y);
    c.lineTo(to.x - len * Math.cos(ang - Math.PI / 7), to.y - len * Math.sin(ang - Math.PI / 7));
    c.lineTo(to.x - len * Math.cos(ang + Math.PI / 7), to.y - len * Math.sin(ang + Math.PI / 7));
    c.closePath();
    c.fillStyle = c.strokeStyle;
    c.fill();
  }

  // ---------------------------------------------------------------------------
  // TRACE BUILDER — run a scene over a t-grid and emit the JSON the gates read.
  // This is what decouples every semantic gate from a browser.
  // ---------------------------------------------------------------------------
  function buildTrace(opts) {
    const o = opts || {};
    if (typeof o.draw !== 'function') fail('buildTrace needs a draw(scene) function');
    const words = o.words !== undefined ? o.words : globalWords();
    // No script given but word timings are: the aligner output IS the script (one source).
    const script = o.script instanceof Script ? o.script
      : ((o.script === undefined || o.script === null) && words ? Script.fromWords(words, o.subjects)
        : new Script(o.script || []));
    const scene = new Scene({ name: o.name || 'scene', script, ctx: null,
                              width: o.width || 1920, height: o.height || 1080,
                              subsystem: o.subsystem || o.system || null,
                              assets: o.assets || [],
                              kit: o.kit, words: o.words });
    const grid = finite(o.grid) ? o.grid : 5;               // samples/second
    const dur = finite(o.duration) ? o.duration : script.duration;
    const repeat = Number.isInteger(o.repeat) && o.repeat > 1 ? o.repeat : 1;
    let times;
    if (Array.isArray(o.times) && o.times.length) {
      times = o.times.map(Number);
      for (const t of times) if (!finite(t)) fail(`buildTrace times contains a non-finite t (${t})`);
    } else {
      times = [];
      const n = Math.floor(dur * grid);
      for (let i = 0; i <= n; i++) times.push(i / grid);
    }
    RNG_LOG.clear();
    VIOLATIONS.length = 0;
    const frames = [];
    const mismatches = [];
    for (const t of times) {
      let first = null;
      for (let r = 0; r < repeat; r++) {
        // render() releases the clock guard even when the draw throws.
        const fr = scene.render(t, o.draw);
        if (r === 0) { first = fr; frames.push(fr); }
        else if (JSON.stringify(fr) !== JSON.stringify(first)) mismatches.push(round3(t));
      }
    }
    const k = scene.kit;
    const widx = scene._wordIndex();
    return {
      scene: scene.name,
      subsystem: scene.subsystem,
      declaredAssets: scene.declaredAssets,
      width: scene.width,
      height: scene.height,
      grid,
      duration: round3(dur),
      lines: script.lines.map(l => ({
        idx: l.idx, name: l.name, text: l.text, subjectId: l.subjectId,
        start: round3(l.start), end: round3(l.end),
      })),
      declaredSections: Array.from(new Set(
        frames.flatMap(f => f.sections.map(s => s.name)))),
      // ---- provenance of every input that shapes a frame (A3 / E2 / E10 / H1) ----
      kit: k ? { id: k.kit || k.id || null, name: k.name || null, mode: k.mode || null } : null,
      words: widx ? { source: widx.source, lines: widx.names.length } : null,
      random: Array.from(RNG_LOG.values()),
      clockGuard: GUARD_ENABLED,
      nondeterminism: scene._violations.slice(),
      forms: Array.from(scene._forms.values()),
      repeat: repeat > 1 ? { n: repeat, identical: mismatches.length === 0, mismatches } : null,
      frames,
    };
  }

  const geometry = { center, edgePoints, boxEdge };

  return {
    Script, Scene, SceneError,
    setVerify, setInstrument, buildTrace,
    easeOutCubic, clamp01,
    // A3 — active style kit
    setKit, kitValue, get kit() { return globalKit(); },
    // E2 — word timings that THROW
    setWords, atLine, atWord, normWord, get words() { return globalWords(); },
    // E10 — seeded randomness + the clock guard
    random, setClockGuard, get clockGuard() { return GUARD_ENABLED; },
    get violations() { return VIOLATIONS.slice(); },
    geometry,
    MIN_LEGIBLE_PX,
    get verify() { return VERIFY; },
    get instrument() { return INSTRUMENT; },
  };
});

// ---------------------------------------------------------------------------
// Node CLI:  node scene_runtime.js selftest
// A built-in self-test so the runtime's own guarantees are provable without a
// browser, and so the Python suite can assert them by exit code.
// ---------------------------------------------------------------------------
if (typeof module === 'object' && require.main === module) {
  const C = module.exports;
  const cmd = process.argv[2] || 'selftest';
  let pass = 0, failn = 0;
  const ok = (name, cond, detail) => {
    if (cond) { pass++; console.log('  ok   ' + name); }
    else { failn++; console.log('  FAIL ' + name + (detail ? '  ' + detail : '')); }
  };
  const throws = (name, fn, needle) => {
    try { fn(); ok(name + ' (expected throw)', false, 'did not throw'); }
    catch (e) {
      ok(name, !needle || String(e.message).includes(needle),
         needle ? `message lacked "${needle}": ${e.message}` : '');
    }
  };

  if (cmd === 'selftest') {
    console.log('[scene_runtime selftest]');
    const lines = [
      { name: 'intro', text: 'First line.', start: 0, end: 2, subjectId: 'case-a' },
      { name: 'worked-example', text: 'Second line.', start: 2, end: 5, subjectId: 'case-a' },
    ];
    const s = new C.Script(lines);

    ok('script length', s.length === 2, String(s.length));
    ok('named beat resolves', s.beat('intro').idx === 0, '');
    ok('index beat resolves', s.beat(1).idx === 1, '');
    throws('unknown beat name THROWS', () => s.beat('nope'), 'unknown beat name');
    throws('out-of-range index THROWS (no clamp)', () => s.beat(7), 'OUT OF RANGE');
    throws('duplicate beat names rejected',
      () => new C.Script([{ name: 'a', start: 0, end: 1 }, { name: 'a', start: 1, end: 2 }]),
      'duplicate beat name');
    throws('non-finite timing rejected',
      () => new C.Script([{ name: 'a', start: 0, end: NaN }]), 'non-finite');

    // boundary array safety (the raw B[i] -> NaN defect)
    ok('bounds length is lines+1', s.bounds().length === 3, '');
    throws('boundary past the array THROWS', () => s.boundary(9), 'out of range');

    // non-finite alpha is fatal at the draw call (S1 at source)
    const sc = new C.Scene({ name: 'x', script: s, ctx: null });
    sc.begin(1.0);
    throws('NaN alpha THROWS at draw', () => sc.text({ text: 'hi', alpha: NaN }), 'non-finite alpha');
    throws('undefined alpha THROWS at draw', () => sc.chip({ text: 'c', x: 0, y: 0 }), 'non-finite alpha');

    // bounded text by default (#4)
    sc.begin(1.0);
    let inner = null;
    sc.card({ id: 'c1', x: 100, y: 100, w: 440, h: 200, alpha: 1, pad: 20 }, (z) => {
      inner = z.text({ text: 'A very long sub-caption that would overflow this card badly', alpha: 1, size: 28 });
    });
    ok('bounded text stays inside its card',
      inner.x >= 100 + 20 - 0.01 && inner.x + inner.w <= 100 + 440 - 20 + 0.01,
      `x=${inner.x} w=${inner.w}`);
    ok('bounded text is marked fitted', inner.fitted === true, JSON.stringify(inner));

    // unbounded is an explicit opt-in that CAN escape (so the gate has something to catch)
    sc.begin(1.0);
    let esc = null;
    sc.card({ id: 'c2', x: 100, y: 100, w: 440, h: 200, alpha: 1 }, (z) => {
      esc = z.text({ text: 'A very long sub-caption that would overflow this card badly',
                     alpha: 1, size: 28, x: 100, unbounded: true });
    });
    ok('unbounded text is allowed to overflow (explicit opt-in)',
      esc.x + esc.w > 100 + 440, `w=${esc.w}`);

    // connectors require declared endpoints (#6)
    sc.begin(1.0);
    sc.node({ id: 'a', x: 100, y: 100, w: 120, h: 60, alpha: 1 });
    sc.node({ id: 'b', x: 500, y: 100, w: 120, h: 60, alpha: 1 });
    throws('connector without from/to THROWS', () => sc.connector({ alpha: 1 }), 'requires from/to');
    throws('connector to unknown node THROWS',
      () => sc.connector({ from: 'a', to: 'ghost', alpha: 1 }), 'unknown node');
    const cn = sc.connector({ from: 'a', to: 'b', alpha: 1 });
    ok('connector records from/to and arrowAt', cn.from === 'a' && cn.to === 'b' && cn.arrowAt === 'to', '');

    // assets require subjectId (#5)
    throws('asset without subjectId THROWS',
      () => sc.asset({ path: 'x.png', x: 0, y: 0, w: 10, h: 10, alpha: 1 }), 'must declare subjectId');

    // sections (#3)
    sc.begin(1.0);
    sc.section('overview', (z) => { z.chip({ text: 'k', x: 10, y: 10, alpha: 1 }); });
    const fr = sc.frame();
    ok('section recorded with a draw', fr.sections.some(x => x.name === 'overview' && x.drawCount > 0),
      JSON.stringify(fr.sections));

    // trace building
    const tr = C.buildTrace({
      name: 'demo', script: lines, grid: 5,
      draw: (z, t) => {
        z.section('main', (q) => {
          q.chip({ text: 'always', x: 40, y: 40, alpha: 1 });
          q.text({ text: 'beat two', x: 40, y: 200, alpha: q.beatAlpha('worked-example') });
        });
      },
    });
    ok('trace has frames', tr.frames.length > 0, '');
    ok('trace declares its sections', tr.declaredSections.includes('main'), '');
    ok('beatAlpha is 0 before its beat and 1 well inside it',
      tr.frames[0].boxes.filter(b => b.kind === 'text')[0].alpha === 0 &&
      tr.frames[tr.frames.length - 1].boxes.filter(b => b.kind === 'text')[0].alpha === 1, '');
    ok('every recorded alpha is finite',
      tr.frames.every(f => f.boxes.every(b => Number.isFinite(b.alpha))), '');

    // ---- A3: the ACTIVE KIT is read by default (window.__C8_KIT__), lazily ----
    const probeKit = { kit: 'probe-kit', card: { fill: '#123456' }, text: { color: '#abcdef' },
                       motion: { fadeIn: 0.9, lead: 0.0 } };
    global.window = Object.assign(global.window || {}, { __C8_KIT__: probeKit });
    const kt = C.buildTrace({ name: 'kit', script: lines, grid: 2, draw: (z) => {
      z.section('k', (q) => q.card({ id: 'kc', x: 100, y: 300, w: 600, h: 200, alpha: q.beatAlpha('worked-example') },
                                  (k2) => k2.text({ text: 'kit text' }))); } });
    const kcard = kt.frames[kt.frames.length - 1].boxes.find(b => b.kind === 'card');
    const ktext = kt.frames[kt.frames.length - 1].boxes.find(b => b.kind === 'text');
    ok('runtime reads window.__C8_KIT__ by default (colour recorded in the trace)',
      kcard && kcard.fill === '#123456' && ktext.color === '#abcdef' && kt.kit && kt.kit.id === 'probe-kit',
      JSON.stringify({ kcard, kit: kt.kit }));
    const sk = new C.Scene({ name: 'k2', script: s, ctx: null }); sk.begin(2.45);
    ok('kit motion (fadeIn/lead) drives beatAlpha with no code edit',
      Math.abs(sk.beatAlpha('worked-example') - C.easeOutCubic(0.45 / 0.9)) < 1e-9, String(sk.beatAlpha('worked-example')));
    sk.end();
    delete global.window.__C8_KIT__;

    // ---- E2: atLine / atWord THROW when the line or word is not there ----
    const W = { source: 'engine', lines: [
      { name: 'intro', text: 'First line here.', start: 0, end: 2,
        words: [{ w: 'First', start: 0.1, end: 0.5, i: 0 }, { w: 'line', start: 0.6, end: 1.0, i: 1 },
                { w: 'here.', start: 1.1, end: 1.6, i: 2 }] }] };
    ok('atWord returns the aligner timing (no estimation)', C.atWord('intro', 'here', 1, W).start === 1.1, '');
    ok('atLine returns the aligned line', C.atLine('intro', W).words.length === 3, '');
    throws('atWord of a word NOT in the line THROWS', () => C.atWord('intro', 'missing', 1, W), 'NOT in that line');
    throws('atLine of an unknown line THROWS', () => C.atLine('nope', W), 'unknown line');
    throws('atWord with no word timings loaded THROWS', () => C.atWord('intro', 'line', 1, null), 'no word timings');

    // ---- E10: seeded PRNG reproduces; the clock guard throws during draw ----
    const r1 = C.random(42), r2 = C.random(42), r3 = C.random(43);
    const a1 = [r1(), r1(), r1()], a2 = [r2(), r2(), r2()];
    ok('C8.random(seed) reproduces the same sequence', a1.join() === a2.join() && a1.join() !== [r3(), r3(), r3()].join(), '');
    throws('random() with no seed THROWS', () => C.random(), 'needs a seed');
    for (const [label, fn] of [['Math.random', () => Math.random()], ['Date.now', () => Date.now()],
                               ['performance.now', () => performance.now()], ['new Date()', () => new Date()]]) {
      throws(`${label} during a frame draw THROWS`,
        () => C.buildTrace({ name: 'g', script: lines, grid: 1, draw: () => { fn(); } }), 'nondeterministic call');
    }
    ok('the clock guard is released after the trace', typeof Math.random() === 'number' &&
      typeof Date.now() === 'number' && typeof performance.now() === 'number' && new Date(0).getTime() === 0, '');
    const sw = C.buildTrace({ name: 'swallow', script: lines, grid: 1, draw: (z) => {
      try { Math.random(); } catch (e) { /* swallowed on purpose */ }
      z.chip({ text: 'x', x: 10, y: 400, alpha: 1 }); } });
    ok('a SWALLOWED clock read is still recorded in the trace', sw.nondeterminism.length > 0, JSON.stringify(sw.nondeterminism));
    const rp = C.buildTrace({ name: 'rep', script: lines, grid: 2, repeat: 2, draw: (z) => {
      const r = z.random(7, 'jitter'); z.chip({ text: 'j', x: 100 + 50 * r(), y: 400, alpha: 1 }); } });
    ok('the same t rendered twice gives an identical frame', rp.repeat && rp.repeat.identical === true, JSON.stringify(rp.repeat));
    ok('every seed used is recorded in the trace', rp.random.length === 1 && rp.random[0].seed === 7 && rp.random[0].maxDraws === 1,
      JSON.stringify(rp.random));
    sc.begin(1.0);
    throws('mark() without a meaning THROWS', () => sc.mark({ shape: 'rect', x: 0, y: 0, w: 5, h: 5, alpha: 1 }), 'needs a');
    sc.end();

    console.log(`\nRESULT: ${pass} passed, ${failn} failed`);
    process.exit(failn ? 1 : 0);
  }

  if (cmd === 'trace') {
    // node scene_runtime.js trace <scene.js> [--out f.json] [--grid N] [--kit kit.json|kit.js]
    //      [--words words.json] [--at t1,t2] [--repeat N] [--no-guard]
    // The scene module exports {draw, script|SCRIPT, name?, subsystem?, assets?, words?, grid?}.
    const fs = require('fs');
    const pathm = require('path');
    const argv = process.argv.slice(3);
    const opt = (k) => { const i = argv.indexOf(k); return i >= 0 ? argv[i + 1] : undefined; };
    const scenePath = argv.find((a, i) => !a.startsWith('--') && (i === 0 || !argv[i - 1].startsWith('--')));
    if (!scenePath) { console.error('usage: node scene_runtime.js trace <scene.js> [--out f] [--kit f] [--words f]'); process.exit(2); }
    const readJson = (f, what) => {
      const txt = fs.readFileSync(f, 'utf8').trim();
      if (txt.startsWith('{')) return JSON.parse(txt);
      const m = txt.match(/__C8_(?:KIT|WORDS)__\s*=\s*(\{[\s\S]*\})\s*;?\s*$/);
      if (!m) throw new Error(`${what} file ${f} is neither JSON nor 'window.__C8_…__ = {…};'`);
      return JSON.parse(m[1]);
    };
    try {
      if (argv.includes('--no-guard')) C.setClockGuard(false);
      if (opt('--kit')) C.setKit(readJson(opt('--kit'), 'kit'));
      const mod = require(pathm.resolve(scenePath));
      const words = opt('--words') ? readJson(opt('--words'), 'words') : mod.words;
      if (words) C.setWords(words);
      const at = opt('--at') ? opt('--at').split(',').map(Number) : undefined;
      const trace = C.buildTrace({
        name: mod.name || pathm.basename(scenePath).replace(/\.[^.]+$/, ''),
        script: mod.script || mod.SCRIPT, draw: mod.draw,
        subsystem: mod.subsystem, assets: mod.assets, subjects: mod.subjects,
        grid: opt('--grid') ? Number(opt('--grid')) : mod.grid,
        duration: mod.duration, times: at,
        repeat: opt('--repeat') ? Number(opt('--repeat')) : undefined,
      });
      const text = JSON.stringify(trace);
      if (opt('--out')) { fs.mkdirSync(pathm.dirname(pathm.resolve(opt('--out'))), { recursive: true }); fs.writeFileSync(opt('--out'), text + '\n'); }
      else process.stdout.write(text + '\n');
      console.error(`[trace] ${trace.scene}: ${trace.frames.length} frames` +
        (trace.kit ? `, kit ${trace.kit.id}` : '') + (trace.words ? `, words ${trace.words.lines} lines` : '') +
        (trace.repeat ? `, repeat identical=${trace.repeat.identical}` : ''));
      process.exit(0);
    } catch (e) {
      console.error(String(e && e.message || e));
      process.exit(1);
    }
  }
  console.error('usage: node scene_runtime.js selftest | trace <scene.js> [--out f] [--kit f] [--words f] [--at t,…] [--repeat N]');
  process.exit(2);
}
