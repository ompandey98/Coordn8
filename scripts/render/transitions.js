/*
 * Coordn8 — TRANSITIONS & CONTINUITY (E9). Zero dependencies; browser and Node.
 *
 * The vocabulary comes from the ACTIVE kit (window.__C8_KIT__.transitions, exported by
 * scripts/design/kit_export.py): each entry is classified into one of these kinds —
 *
 *   morph     same-element morph: elements that share an `id` across two consecutive scenes
 *             INTERPOLATE position, size, colour, alpha and radius instead of cutting, so the
 *             eye follows the object. Unmatched outgoing elements fade and lift away;
 *             unmatched incoming elements fade in after the matched ones are under way.
 *             p = 0 reproduces scene A exactly and p = 1 reproduces scene B exactly, so the last
 *             frame of A and the first frame of B are the same picture.
 *   dissolve  cross-fade of the two scenes.
 *   cut       hard cut; `on_beat` cuts snap to a measured beat (and to a frame boundary).
 *   push      the outgoing scene is pushed off by the incoming one along `move` {x, y}.
 *   wipe      a mask reveals the incoming scene (`mask`: wipe-left/right/up/down).
 *   dip       fade through a colour (`via`) — the section-change signal.
 *   zoom      scale through (scale_from -> scale_to) while cross-fading.
 *
 * A transition that is NOT in the kit is still drawn (pass `kind` and its params) — the kit is a
 * menu, never a fence; `compose()` only reports `in_kit: false`.
 *
 * Every function is a PURE FUNCTION OF t: no clock, no randomness, no CSS animation, so any
 * frame re-renders identically in isolation.
 *
 * First-second anchor: anchorAlpha()/anchorState() guarantee a visible anchor (the title or the
 * persistent object) within the first 1.0 s of the video — by default fully visible at frame 0.
 */
'use strict';

(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.C8Transitions = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this),
function (root) {
  const FIRST_SECOND = 1.0;
  const VISIBLE_ALPHA = 0.5;

  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function lerp(a, b, p) { return a + (b - a) * p; }

  // ------------------------------------------------------------------ easing
  /** CSS cubic-bezier(x1,y1,x2,y2) as a function of x in [0,1] (bisection; deterministic). */
  function bezier(pts) {
    const [x1, y1, x2, y2] = pts;
    const bx = s => 3 * x1 * s * (1 - s) * (1 - s) + 3 * x2 * s * s * (1 - s) + s * s * s;
    const by = s => 3 * y1 * s * (1 - s) * (1 - s) + 3 * y2 * s * s * (1 - s) + s * s * s;
    return x => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let lo = 0, hi = 1, s = x;
      for (let i = 0; i < 40; i++) {
        s = (lo + hi) / 2;
        if (bx(s) < x) lo = s; else hi = s;
      }
      return by(s);
    };
  }
  function easeInOutCubic(x) { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }

  /** The ease for a transition entry, else the kit's motion ease, else ease-in-out cubic. */
  function easeFor(entry, kit) {
    const e = entry && entry.ease;
    if (Array.isArray(e) && e.length === 4 && e.every(finite)) return bezier(e);
    const k = kit || (root && root.__C8_KIT__);
    const m = k && k.motion;
    if (m && Array.isArray(m.ease) && m.ease.length === 4) return bezier(m.ease);
    return easeInOutCubic;
  }

  function progress(t, start, dur, ease) {
    if (!finite(t)) throw new Error('[coordn8:transitions] non-finite t=' + t);
    if (!(dur > 0)) return t >= start ? 1 : 0;
    const p = clamp01((t - start) / dur);
    return (ease || easeInOutCubic)(p);
  }

  // ------------------------------------------------------------------ vocabulary
  function kindOf(entry) {
    const e = entry || {};
    if (e.kind) return e.kind;
    const id = String(e.id || '').toLowerCase();
    if (e.morph) return 'morph';
    const secs = finite(e.s) ? e.s : (finite(e.ms) ? e.ms / 1000 : null);
    if (e.on_beat || secs === 0 || /\bcut\b|^cut/.test(id)) return 'cut';
    if (e.mask || /wipe|iris/.test(id)) return 'wipe';
    if (e.move || /push|slide/.test(id)) return 'push';
    if (e.blend === 'via' || e.via_role || e.via || /through|dip/.test(id)) return 'dip';
    if ((finite(e.scale_from) && finite(e.scale_to) && e.scale_from !== e.scale_to) || /zoom/.test(id)) return 'zoom';
    if (e.blend === 'crossfade' || /dissolve|fade/.test(id)) return 'dissolve';
    if (/morph/.test(id)) return 'morph';
    return 'dissolve';
  }

  function vocabulary(kit) {
    const k = kit || (root && root.__C8_KIT__) || {};
    return (k.transitions || []).map(e => Object.assign({}, e, { kind: kindOf(e),
      s: finite(e.s) ? e.s : (finite(e.ms) ? e.ms / 1000 : 0.45) }));
  }

  /** The kit entry for an id or a kind ('morph', 'dissolve', 'cut-on-beat', 'push' …), or null. */
  function pick(ref, kit) {
    const v = vocabulary(kit);
    return v.find(e => e.id === ref) || v.find(e => e.kind === ref) ||
      (ref === 'cut-on-beat' ? v.find(e => e.kind === 'cut' && e.on_beat) : null) || null;
  }

  // ------------------------------------------------------------------ colour
  function parseColor(c) {
    if (typeof c !== 'string') return null;
    const s = c.trim();
    let m = /^#([0-9a-f]{3})$/i.exec(s);
    if (m) return [...m[1]].map(h => parseInt(h + h, 16)).concat([1]);
    m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(s);
    if (m) return [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16)).concat([m[2] ? parseInt(m[2], 16) / 255 : 1]);
    m = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i.exec(s);
    if (m) return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
    return null;
  }
  function hex2(n) { return Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0'); }
  function mixColor(a, b, p) {
    if (a === b || p <= 0) return a;
    if (p >= 1) return b;
    const A = parseColor(a), B = parseColor(b);
    if (!A || !B) return p < 0.5 ? a : b;
    const c = [0, 1, 2, 3].map(i => lerp(A[i], B[i], p));
    if (A[3] === 1 && B[3] === 1) return '#' + hex2(c[0]) + hex2(c[1]) + hex2(c[2]);
    return `rgba(${Math.round(c[0])},${Math.round(c[1])},${Math.round(c[2])},${Math.round(c[3] * 1000) / 1000})`;
  }

  // ------------------------------------------------------------------ morph
  const NUM_KEYS = ['x', 'y', 'w', 'h', 'radius', 'rotation', 'size', 'scale'];

  /**
   * Same-element morph of two element lists at eased progress p (0..1). Matched by `id`.
   * Returns a new list (inputs untouched); each element gains `morph: 'matched'|'out'|'in'`.
   */
  function morph(from, to, p, opts) {
    const o = opts || {};
    const lift = finite(o.lift) ? o.lift : 30;
    const byId = new Map();
    for (const e of from || []) if (e && e.id !== undefined && e.id !== null) byId.set(e.id, e);
    const toIds = new Set((to || []).filter(e => e && e.id !== undefined && e.id !== null).map(e => e.id));
    const out = [];
    for (const a of from || []) {
      if (a && a.id !== undefined && a.id !== null && toIds.has(a.id)) continue;   // drawn from the B side
      const alpha = (finite(a.alpha) ? a.alpha : 1) * (1 - clamp01(p * 1.6));
      out.push(Object.assign({}, a, { alpha, y: (a.y || 0) - lift * p, morph: 'out', layer: 'from' }));
    }
    for (const b of to || []) {
      const a = b && byId.get(b.id);
      if (a) {
        const e = Object.assign({}, b, { morph: 'matched', layer: 'both' });
        for (const k of NUM_KEYS) if (finite(a[k]) && finite(b[k])) e[k] = lerp(a[k], b[k], p);
        const aa = finite(a.alpha) ? a.alpha : 1, ba = finite(b.alpha) ? b.alpha : 1;
        e.alpha = lerp(aa, ba, p);
        for (const k of ['color', 'fill', 'stroke']) if (a[k] !== undefined && b[k] !== undefined) e[k] = mixColor(a[k], b[k], p);
        if (a.text !== undefined && b.text !== undefined && a.text !== b.text) e.text = p < 0.5 ? a.text : b.text;
        out.push(e);
      } else {
        const alpha = (finite(b.alpha) ? b.alpha : 1) * clamp01((p - 0.35) / 0.65);
        out.push(Object.assign({}, b, { alpha, y: (b.y || 0) + lift * (1 - p), morph: 'in', layer: 'to' }));
      }
    }
    return out;
  }

  function sharedIds(from, to) {
    const a = new Set((from || []).map(e => e && e.id).filter(x => x !== undefined && x !== null));
    return (to || []).map(e => e && e.id).filter(x => x !== undefined && x !== null && a.has(x));
  }

  // ------------------------------------------------------------------ reveal point
  /** Fraction of the transition at which the incoming scene reads as "arrived". */
  function revealPoint(kind, ease) {
    if (kind === 'cut') return 0;
    if (kind === 'dissolve' || kind === 'dip' || kind === 'wipe') return 0.5;
    const f = ease || easeInOutCubic;
    for (let i = 0; i <= 400; i++) if (f(i / 400) >= 0.9) return i / 400;   // ~90% of the move
    return 1;
  }

  /** Start time so the reveal lands ON the beat: start = beat - reveal * duration. */
  function startForBeat(beat, entry, kit) {
    const e = entry || {};
    const kind = kindOf(e);
    const dur = finite(e.s) ? e.s : (finite(e.ms) ? e.ms / 1000 : 0);
    return beat - revealPoint(kind, easeFor(e, kit)) * dur;
  }

  // ------------------------------------------------------------------ compose
  /**
   * The state of the frame at time t while scene A hands over to scene B.
   * spec: {from:[el], to:[el], at (s), dur (s), kind | id, kit, width, height, beat, fps,
   *        move:{x,y}, via, mask, scale_from, scale_to}
   * Returns {kind, id, in_kit, p, start, dur, elements, overlay, notes}.
   */
  function compose(t, spec) {
    const s = spec || {};
    const kit = s.kit || (root && root.__C8_KIT__) || null;
    const byId = s.id ? pick(s.id, kit) : null;
    // an invented id still borrows the kit's parameters for its kind, but is reported as not in the kit
    const entry = byId || (s.kind && pick(s.kind, kit)) || null;
    const e = Object.assign({}, entry || {}, s.entry || {});
    for (const k of ['move', 'via', 'mask', 'scale_from', 'scale_to', 'on_beat']) if (s[k] !== undefined) e[k] = s[k];
    const kind = s.kind && s.kind !== 'cut-on-beat' ? s.kind : (entry ? kindOf(entry) : (s.kind === 'cut-on-beat' ? 'cut' : 'dissolve'));
    const dur = finite(s.dur) ? s.dur : (finite(e.s) ? e.s : 0.45);
    const ease = easeFor(e, kit);
    let start = finite(s.at) ? s.at : 0;
    const notes = [];
    if (finite(s.beat)) {
      start = kind === 'cut' ? s.beat : startForBeat(s.beat, Object.assign({}, e, { s: dur }), kit);
    }
    if (kind === 'cut' && finite(s.fps) && s.fps > 0) start = Math.round(start * s.fps) / s.fps;
    const inKit = s.id ? !!byId : !!entry;
    if (!inKit) notes.push(`transition '${s.id || s.kind}' is not in the active kit's vocabulary — drawn anyway (kits are menus)`);
    const W = s.width || 1920, H = s.height || 1080;
    const from = s.from || [], to = s.to || [];
    let p, elements = [], overlay = null;

    if (kind === 'cut') {
      p = t >= start ? 1 : 0;
      elements = (p ? to : from).map(x => Object.assign({}, x, { layer: p ? 'to' : 'from' }));
    } else {
      p = progress(t, start, dur, ease);
      if (kind === 'morph') {
        elements = morph(from, to, p, s);
      } else if (kind === 'dissolve') {
        elements = from.map(x => Object.assign({}, x, { alpha: (finite(x.alpha) ? x.alpha : 1) * (1 - p), layer: 'from' }))
          .concat(to.map(x => Object.assign({}, x, { alpha: (finite(x.alpha) ? x.alpha : 1) * p, layer: 'to' })));
      } else if (kind === 'push') {
        const mv = e.move || { x: -1, y: 0 };
        const dx = (mv.x || 0) * W, dy = (mv.y || 0) * H;
        elements = from.map(x => Object.assign({}, x, { x: (x.x || 0) + dx * p, y: (x.y || 0) + dy * p, layer: 'from' }))
          .concat(to.map(x => Object.assign({}, x, { x: (x.x || 0) + dx * (p - 1), y: (x.y || 0) + dy * (p - 1), layer: 'to' })));
      } else if (kind === 'wipe') {
        const mask = String(e.mask || 'wipe-right');
        elements = from.map(x => Object.assign({}, x, { layer: 'from' }))
          .concat(to.map(x => Object.assign({}, x, { layer: 'to', clip: { mask, p } })));
        overlay = { mask, p };
      } else if (kind === 'dip') {
        const via = e.via || (kit && kit.colors && kit.colors.paper) || '#000000';
        if (p < 0.5) {
          elements = from.map(x => Object.assign({}, x, { alpha: (finite(x.alpha) ? x.alpha : 1) * (1 - 2 * p), layer: 'from' }));
          overlay = { color: via, alpha: 2 * p };
        } else {
          elements = to.map(x => Object.assign({}, x, { alpha: (finite(x.alpha) ? x.alpha : 1) * (2 * p - 1), layer: 'to' }));
          overlay = { color: via, alpha: 2 - 2 * p };
        }
      } else if (kind === 'zoom') {
        const s0 = finite(e.scale_from) ? e.scale_from : 1, s1 = finite(e.scale_to) ? e.scale_to : 1.8;
        const sc = lerp(s0, s1, p);
        const cx = W / 2, cy = H / 2;
        elements = from.map(x => Object.assign({}, x, {
          x: cx + ((x.x || 0) - cx) * sc, y: cy + ((x.y || 0) - cy) * sc,
          w: (x.w || 0) * sc, h: (x.h || 0) * sc, alpha: (finite(x.alpha) ? x.alpha : 1) * (1 - p), layer: 'from' }))
          .concat(to.map(x => Object.assign({}, x, { alpha: (finite(x.alpha) ? x.alpha : 1) * p, layer: 'to' })));
      } else {
        throw new Error(`[coordn8:transitions] unknown transition kind '${kind}' — use morph, dissolve, cut, push, wipe, dip or zoom`);
      }
    }
    const shared = sharedIds(from, to);
    if (shared.length && kind !== 'morph') {
      notes.push(`scenes share element id(s) ${shared.join(', ')} but the transition is '${kind}' — a morph would carry them`);
    }
    return { kind, id: s.id || (entry && entry.id) || kind, in_kit: inKit, p, start, dur, elements, overlay, notes, shared };
  }

  // ------------------------------------------------------------------ first-second anchor
  /**
   * Alpha of the anchor element at t. spec: {enter (s, default 0), fadeIn (s, default 0 = fully
   * visible at frame 0), until (s | null), fadeOut (s)}. The anchor is always fully visible by
   * FIRST_SECOND: a late `enter` is pulled forward and reported.
   */
  function anchorAlpha(t, spec) {
    const s = spec || {};
    const fadeIn = finite(s.fadeIn) ? Math.max(0, Math.min(0.5, s.fadeIn)) : 0;
    let enter = finite(s.enter) ? s.enter : 0;
    if (enter + fadeIn > FIRST_SECOND) enter = Math.max(0, FIRST_SECOND - fadeIn);
    let a = fadeIn > 0 ? clamp01((t - enter) / fadeIn) : (t >= enter ? 1 : 0);
    if (finite(s.until)) {
      const fo = finite(s.fadeOut) ? s.fadeOut : 0.3;
      a = Math.min(a, fo > 0 ? 1 - clamp01((t - s.until) / fo) : (t < s.until ? 1 : 0));
    }
    return a;
  }

  function anchorState(t, spec) {
    const s = spec || {};
    const requested = finite(s.enter) ? s.enter : 0;
    const fadeIn = finite(s.fadeIn) ? Math.max(0, Math.min(0.5, s.fadeIn)) : 0;
    const pulled = requested + fadeIn > FIRST_SECOND;
    return Object.assign({ id: s.id || 'anchor', kind: s.kind || 'anchor' }, s.element || {}, {
      alpha: anchorAlpha(t, s), anchor: true,
      note: pulled ? `anchor entrance at ${requested}s was pulled forward so it is visible by ${FIRST_SECOND}s` : null });
  }

  /** First t at which some element is visible (alpha >= 0.5, non-trivial area). frames: [{t, elements|boxes}] */
  function firstVisible(frames, opts) {
    const o = opts || {};
    const minArea = finite(o.minArea) ? o.minArea : 1920 * 1080 * 0.0025;
    const skip = new Set(o.ignoreKinds || ['background', 'caption-scrim', 'scrim', 'vignette']);
    for (const f of (frames || []).slice().sort((a, b) => a.t - b.t)) {
      const els = f.elements || f.boxes || [];
      if (els.some(e => !skip.has(e.kind) && (finite(e.alpha) ? e.alpha : 1) >= VISIBLE_ALPHA &&
          (e.w || 0) * (e.h || 0) >= minArea)) return f.t;
    }
    return null;
  }

  // ------------------------------------------------------------------ paint (convenience)
  function paint(ctx, state) {
    if (!ctx || !state) return;
    ctx.save();
    for (const e of state.elements) {
      const a = finite(e.alpha) ? e.alpha : 1;
      if (a <= 0.001) continue;
      ctx.globalAlpha = a;
      ctx.fillStyle = e.fill || e.color || '#cccccc';
      if (e.clip && ctx.save) {
        ctx.save();
        const p = e.clip.p, W = ctx.canvas ? ctx.canvas.width : 1920, H = ctx.canvas ? ctx.canvas.height : 1080;
        ctx.beginPath();
        if (/left/.test(e.clip.mask)) ctx.rect(W * (1 - p), 0, W * p, H);
        else if (/up/.test(e.clip.mask)) ctx.rect(0, H * (1 - p), W, H * p);
        else if (/down/.test(e.clip.mask)) ctx.rect(0, 0, W, H * p);
        else ctx.rect(0, 0, W * p, H);
        ctx.clip();
      }
      if (e.text !== undefined && !e.w) {
        ctx.font = `${e.weight || 600} ${e.size || 44}px ${e.font || 'Inter, system-ui, sans-serif'}`;
        ctx.textBaseline = 'top'; ctx.fillText(String(e.text), e.x || 0, e.y || 0);
      } else if (ctx.roundRect && e.radius) {
        ctx.beginPath(); ctx.roundRect(e.x || 0, e.y || 0, e.w || 0, e.h || 0, e.radius); ctx.fill();
      } else ctx.fillRect(e.x || 0, e.y || 0, e.w || 0, e.h || 0);
      if (e.clip && ctx.restore) ctx.restore();
    }
    if (state.overlay && state.overlay.color) {
      ctx.globalAlpha = state.overlay.alpha; ctx.fillStyle = state.overlay.color;
      ctx.fillRect(0, 0, ctx.canvas ? ctx.canvas.width : 1920, ctx.canvas ? ctx.canvas.height : 1080);
    }
    ctx.restore();
  }

  return { FIRST_SECOND, VISIBLE_ALPHA, bezier, easeFor, progress, kindOf, vocabulary, pick,
           mixColor, morph, sharedIds, revealPoint, startForBeat, compose, anchorAlpha, anchorState,
           firstVisible, paint };
});

// ---------------------------------------------------------------------------
// Node CLI:  node transitions.js compose '<json {t, spec}>' | anchor '<json {t, spec}>'
// ---------------------------------------------------------------------------
if (typeof module === 'object' && typeof require === 'function' && require.main === module) {
  const T = module.exports;
  const [cmd, arg] = process.argv.slice(2);
  const o = JSON.parse(arg || '{}');
  let out;
  if (cmd === 'compose') out = T.compose(o.t, o.spec || {});
  else if (cmd === 'anchor') out = T.anchorState(o.t, o.spec || {});
  else if (cmd === 'reveal') out = { start: T.startForBeat(o.beat, o.entry || {}, o.kit) };
  else { process.stderr.write('usage: node transitions.js compose|anchor|reveal <json>\n'); process.exit(2); }
  process.stdout.write(JSON.stringify(out) + '\n');
}
