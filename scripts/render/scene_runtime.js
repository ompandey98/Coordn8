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
  if (typeof window !== 'undefined') window.Coordn8 = api;
})(this, function () {

  // ---------------------------------------------------------------------------
  // Verify mode: ON by default everywhere except an explicit production render.
  // The whole point is that a mistake is LOUD at authoring time, so the default
  // must be strict. `Coordn8.setVerify(false)` is the deliberate opt-out.
  // ---------------------------------------------------------------------------
  let VERIFY = true;
  let INSTRUMENT = false;

  function setVerify(v) { VERIFY = !!v; }
  function setInstrument(v) { INSTRUMENT = !!v; }

  class SceneError extends Error {
    constructor(msg) { super('[coordn8:scene] ' + msg); this.name = 'SceneError'; }
  }

  function fail(msg) { throw new SceneError(msg); }

  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }

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
      this.t = 0;
      this._boxes = [];
      this._connectors = [];
      this._assets = [];
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
      this._sectionStack = [];
      this._containerStack = [];
      this._sectionsSeen = new Map();
      if (this.ctx) { this.ctx.save(); this.ctx.clearRect(0, 0, this.width, this.height); }
      return this;
    }

    end() {
      if (this.ctx) this.ctx.restore();
      return this.frame();
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
      const fadeIn = finite(o.fadeIn) ? o.fadeIn : 0.35;
      const lead = finite(o.lead) ? o.lead : 0.12;       // ~120ms anticipation (PLAYBOOK 2.3)
      const holdUntil = o.until === undefined ? null : o.until;
      const start = ln.start - lead;
      let a = easeOutCubic((this.t - start) / Math.max(1e-6, fadeIn));
      if (holdUntil !== null) {
        const out = this.beat(holdUntil);                // also throws if unknown
        const fadeOut = finite(o.fadeOut) ? o.fadeOut : 0.3;
        const outA = 1 - easeOutCubic((this.t - out.start) / Math.max(1e-6, fadeOut));
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
        pad: finite(s.pad) ? s.pad : 18,
      });
      if (s.id) this._nodes.set(s.id, { x: s.x, y: s.y, w: s.w, h: s.h });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = s.fill || 'rgba(255,255,255,0.06)';
        if (c.roundRect) { c.beginPath(); c.roundRect(box.x, box.y, box.w, box.h, s.radius || 14); c.fill(); }
        else c.fillRect(box.x, box.y, box.w, box.h);
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
      const size = finite(s.size) ? s.size : 28;
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
      let w = this._measure(str, size);
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
          // Shrink to fit, down to a legibility floor; below that, ellipsize.
          const minSize = finite(s.minSize) ? s.minSize : Math.max(14, Math.round(size * 0.62));
          let trySize = size;
          while (trySize > minSize && this._measure(str, trySize) > inner) trySize -= 1;
          if (this._measure(str, trySize) > inner) {
            display = this._ellipsize(str, trySize, inner);
            truncated = true;
          }
          fitted = true;
          w = Math.min(inner, this._measure(display, trySize));
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
      });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = s.color || '#f2f4f8';
        c.font = `${s.weight || 500} ${box.size}px ${s.font || 'Inter, system-ui, sans-serif'}`;
        c.textBaseline = 'top';
        c.fillText(display, box.x, box.y);
        c.restore();
      }
      return box;
    }

    _measure(str, size) {
      if (this.ctx) {
        const c = this.ctx;
        c.save();
        c.font = `500 ${size}px Inter, system-ui, sans-serif`;
        const m = c.measureText(str).width;
        c.restore();
        if (finite(m) && m > 0) return m;
      }
      // Deterministic fallback (~0.54em average advance for UI sans at these sizes).
      return str.length * size * 0.54;
    }

    _ellipsize(str, size, maxW) {
      let s = str;
      while (s.length > 1 && this._measure(s + '…', size) > maxW) s = s.slice(0, -1);
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
      const size = finite(s.size) ? s.size : 20;
      const label = String(s.text === undefined ? '' : s.text);
      const padX = finite(s.padX) ? s.padX : 14;
      const w = finite(s.w) ? s.w : this._measure(label, size) + padX * 2;
      const h = finite(s.h) ? s.h : Math.ceil(size * 1.9);
      const box = this._record({
        kind: 'chip', x: s.x, y: s.y, w, h, alpha: s.alpha, text: label, size,
      });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = s.fill || 'rgba(120,170,255,0.16)';
        if (c.roundRect) { c.beginPath(); c.roundRect(box.x, box.y, w, h, h / 2); c.fill(); }
        else c.fillRect(box.x, box.y, w, h);
        c.fillStyle = s.color || '#dce8ff';
        c.font = `600 ${size}px ${s.font || 'Inter, system-ui, sans-serif'}`;
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
      });
      this._nodes.set(s.id, { x: s.x, y: s.y, w: s.w, h: s.h });
      if (this.ctx && box.alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = box.alpha;
        c.fillStyle = s.fill || 'rgba(255,255,255,0.10)';
        if (c.roundRect) { c.beginPath(); c.roundRect(s.x, s.y, s.w, s.h, s.radius || 12); c.fill(); }
        else c.fillRect(s.x, s.y, s.w, s.h);
        c.restore();
      }
      if (s.text) {
        this._containerStack.push({ id: s.id, box: Object.assign({}, box, { pad: 12 }) });
        try { this.text({ text: s.text, alpha: box.alpha, align: 'center', size: s.textSize || 22,
                          y: s.y + s.h / 2 - 14 }); } finally { this._containerStack.pop(); }
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
      };
      if (INSTRUMENT || !this.ctx) this._connectors.push(rec);
      if (this.ctx && alpha > 0.001) {
        const c = this.ctx;
        c.save(); c.globalAlpha = alpha;
        c.strokeStyle = s.color || 'rgba(160,200,255,0.75)';
        c.lineWidth = s.width || 3;
        c.beginPath(); c.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i++) c.lineTo(points[i].x, points[i].y);
        c.stroke();
        // Arrowhead at the declared end, so picture and declaration cannot disagree.
        const tip = rec.arrowAt === 'to' ? points[points.length - 1] : points[0];
        const prev = rec.arrowAt === 'to' ? points[points.length - 2] : points[1];
        if (prev) drawArrow(c, prev, tip, s.width || 3);
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
      const scale = finite(s.scale) ? s.scale : 1;
      const box = this._record({
        kind: 'shot', x: s.x, y: s.y, w: s.w, h: s.h, alpha: s.alpha,
        path: s.path, subjectId: s.subjectId || s.subject_id, scale,
      });
      const rec = {
        path: s.path, subjectId: box.subjectId, crop: s.crop || null,
        scale, alpha: box.alpha, x: s.x, y: s.y, w: s.w, h: s.h,
        section: this._currentSection(), caption: s.caption || null,
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
    const script = o.script instanceof Script ? o.script : new Script(o.script || []);
    const scene = new Scene({ name: o.name || 'scene', script, ctx: null,
                              width: o.width || 1920, height: o.height || 1080 });
    const grid = finite(o.grid) ? o.grid : 5;               // samples/second
    const dur = finite(o.duration) ? o.duration : script.duration;
    const frames = [];
    const n = Math.floor(dur * grid);
    for (let i = 0; i <= n; i++) {
      const t = i / grid;
      scene.begin(t);
      o.draw(scene, t);
      frames.push(scene.end());
    }
    return {
      scene: scene.name,
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
      frames,
    };
  }

  return {
    Script, Scene, SceneError,
    setVerify, setInstrument, buildTrace,
    easeOutCubic, clamp01,
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

    console.log(`\nRESULT: ${pass} passed, ${failn} failed`);
    process.exit(failn ? 1 : 0);
  }
  console.error('usage: node scene_runtime.js selftest');
  process.exit(2);
}
