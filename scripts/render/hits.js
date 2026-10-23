/*
 * Coordn8 — KEY-REVEAL HIT + BEAT GRID (E3), scene side. Zero dependencies; browser and Node.
 * The same numbers as scripts/render/hits.py (the tests compare the two frame for frame).
 *
 * HOW A SCENE USES IT (the documented import)
 * -------------------------------------------
 *   Browser:  <script src="scene_runtime.js"></script>
 *             <script src="hits.js"></script>          // exposes window.C8Hits
 *   Node:     const C8Hits = require('./hits.js');     // finds ./scene_runtime.js itself
 *
 *   // once, outside draw(): release on the measured start of the key word
 *   const HIT = C8Hits.hitAtWord('the-answer', 'cache', { fps: 30 });
 *   // or on a measured downbeat near it (grid from two measured downbeats, see hits.py)
 *   const HIT2 = C8Hits.hitAtWord('the-answer', 'cache', { fps: 30, release: 'downbeat', grid });
 *
 *   function draw(t) {
 *     const st = C8Hits.stateAt(HIT, t);
 *     const tt = C8Hits.sceneTime(HIT, t);     // frozen for the 3 hold frames
 *     drawEverything(tt);                       // the scene as usual, at tt
 *     overlay(0, 0, W, H, '#000', st.dark);     // pre-darken (≈94 % at its deepest)
 *     keyWord.scale = st.scale;                 // 1.12 -> 1 from the release
 *     glow(st.glow); ring(st.ring);             // burst
 *   }
 *
 * hitAtWord() resolves the word with Coordn8.atWord(), so a narration edit that removes the
 * key word THROWS here instead of releasing the hit on nothing. Every function is a pure
 * function of t / frame: no clock, no randomness.
 *
 * Timeline (defaults): pre-darken starts 0.75 s before the release; the last 3 frames before
 * the release are held; the release frame is the key word's start (or the snapped downbeat);
 * the darkness lifts in 0.18 s; the burst (glow, scale, ring) runs 0.5 s.
 */
'use strict';

(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.C8Hits = api;
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this),
function (root) {
  const DEFAULTS = { pre_darken_s: 0.75, dark_alpha: 0.94, hold_frames: 3, burst_s: 0.5,
                     lift_s: 0.18, scale_from: 1.12 };
  const FRAME_TOLERANCE = 2;

  function finite(n) { return typeof n === 'number' && Number.isFinite(n); }
  function need(n, what) {
    if (!finite(n)) throw new Error(`[coordn8:hits] ${what} must be a finite number (got ${n})`);
    return n;
  }
  function frameOf(t, fps) { return Math.floor(t * fps + 0.5); }
  function smooth(p) { p = p < 0 ? 0 : (p > 1 ? 1 : p); return p * p * (3 - 2 * p); }
  function easeOut(p) { p = p < 0 ? 0 : (p > 1 ? 1 : p); return 1 - Math.pow(1 - p, 3); }
  function r6(x) { return Math.round(x * 1e6) / 1e6; }

  // ------------------------------------------------------------------ beat grid
  /** Grid from two MEASURED downbeats d0 < d1: bar = (d1 - d0) / whole number of bars. */
  function beatGrid(d0, d1, opts) {
    const o = opts || {};
    need(d0, 'd0'); need(d1, 'd1');
    const span = d1 - d0;
    if (span <= 0) throw new Error('[coordn8:hits] the second downbeat must come after the first');
    let n = o.bars_between;
    if (n == null) {
      if (!finite(o.approx_bar_s) || o.approx_bar_s <= 0) {
        throw new Error('[coordn8:hits] give bars_between or approx_bar_s');
      }
      n = Math.max(1, Math.round(span / o.approx_bar_s));
    }
    const bpb = o.beats_per_bar || 4;
    const bar = span / n;
    return { origin_s: d0, bar_s: bar, beat_s: bar / bpb, beats_per_bar: bpb, bars_between: n,
             bpm: Math.round(60 * bpb / bar * 1000) / 1000, from_downbeats: [d0, d1] };
  }
  function gridBeat(g, k) { return g.origin_s + k * g.beat_s; }
  function gridDownbeat(g, k) { return g.origin_s + k * g.bar_s; }
  function nearest(g, t, downbeat) {
    const step = downbeat === false ? g.beat_s : g.bar_s;
    const k = Math.round((t - g.origin_s) / step);
    return g.origin_s + k * step;
  }

  // ------------------------------------------------------------------ hit timeline
  function hitTimeline(keyT, opts) {
    const o = Object.assign({}, DEFAULTS);
    const a = opts || {};
    for (const k of Object.keys(DEFAULTS)) if (a[k] != null) o[k] = a[k];
    const fps = a.fps || 30;
    need(keyT, 'key time');
    let relT = keyT, src = 'word';
    if (a.release === 'downbeat') {
      const win = a.snap_window_s == null ? 0.25 : a.snap_window_s;
      if (a.grid) {
        const d = nearest(a.grid, keyT, true);
        if (Math.abs(d - keyT) <= win) { relT = d; src = 'downbeat'; }
        else src = `word (no downbeat within ${win}s)`;
      } else src = 'word (no grid given)';
    }
    const rf = frameOf(relT, fps);
    return { fps, key_t: keyT, release_t: relT, release_source: src,
             dark_start_frame: frameOf(relT - o.pre_darken_s, fps),
             hold_start_frame: rf - o.hold_frames,
             release_frame: rf,
             lift_end_frame: rf + Math.max(1, Math.round(o.lift_s * fps)),
             burst_end_frame: rf + Math.max(1, Math.round(o.burst_s * fps)),
             opts: o };
  }

  function hitState(tl, frame) {
    const o = tl.opts;
    const ds = tl.dark_start_frame, hs = tl.hold_start_frame, rf = tl.release_frame;
    const le = tl.lift_end_frame, be = tl.burst_end_frame;
    const st = { frame, dark: 0, frozen: false, scene_frame: frame, burst: 0, glow: 0, scale: 1,
                 ring: 0, phase: 'idle' };
    if (frame >= ds && frame < hs) {
      st.dark = o.dark_alpha * smooth((frame - ds) / Math.max(1, hs - ds)); st.phase = 'pre-darken';
    } else if (frame >= hs && frame < rf) {
      st.dark = o.dark_alpha; st.frozen = true; st.scene_frame = hs; st.phase = 'hold';
    } else if (frame >= rf && frame < be) {
      const p = (frame - rf) / Math.max(1, be - rf);
      const lift = (frame - rf) / Math.max(1, le - rf);
      st.dark = frame < le ? o.dark_alpha * (1 - easeOut(lift)) : 0;
      st.burst = r6(p); st.glow = r6(1 - easeOut(p));
      st.scale = r6(o.scale_from + (1 - o.scale_from) * easeOut(p)); st.ring = r6(easeOut(p));
      st.phase = 'burst';
    }
    st.dark = r6(st.dark);
    return st;
  }

  function stateAt(tl, t) { return hitState(tl, frameOf(need(t, 't'), tl.fps)); }
  /** The time the SCENE should be drawn at: frozen on the hold-start frame for the hold. */
  function sceneTime(tl, t) {
    const st = stateAt(tl, t);
    return st.frozen ? st.scene_frame / tl.fps : t;
  }

  function runtime() {
    if (root && root.Coordn8 && typeof root.Coordn8.atWord === 'function') return root.Coordn8;
    if (typeof require === 'function') {
      try { return require('./scene_runtime.js'); } catch (e) { /* fall through */ }
    }
    throw new Error('[coordn8:hits] hitAtWord needs scene_runtime.js loaded first');
  }

  /** Hit released on the measured start of `word` in line `name`. THROWS (via atWord) when
   *  the line or the word is not in the word timings. opts.words overrides the loaded timings. */
  function hitAtWord(name, word, opts) {
    const o = opts || {};
    const w = runtime().atWord(name, word, o.occurrence, o.words);
    const tl = hitTimeline(w.start, o);
    tl.word = { line: name, w: w.w, start: w.start, end: w.end };
    return tl;
  }

  return { DEFAULTS, FRAME_TOLERANCE, frameOf, beatGrid, gridBeat, gridDownbeat, nearest,
           hitTimeline, hitState, stateAt, sceneTime, hitAtWord };
});

if (typeof module === 'object' && typeof require === 'function' && require.main === module) {
  const H = module.exports;
  const cmd = process.argv[2];
  if (cmd === 'timeline') {
    const a = JSON.parse(process.argv[3] || '{}');
    const tl = H.hitTimeline(a.key_t, a);
    const out = { timeline: tl };
    if (a.frames) out.states = a.frames.map(f => H.hitState(tl, f));
    process.stdout.write(JSON.stringify(out) + '\n');
  } else if (cmd === 'grid') {
    const a = JSON.parse(process.argv[3] || '{}');
    const g = H.beatGrid(a.d0, a.d1, a);
    const ks = a.beats || [];
    process.stdout.write(JSON.stringify({ grid: g, beats: ks.map(k => H.gridBeat(g, k)) }) + '\n');
  } else {
    process.stdout.write('usage: node hits.js timeline \'{"key_t":4.2,"fps":30}\' | grid \'{"d0":1,"d1":9,"approx_bar_s":2}\'\n');
  }
}
