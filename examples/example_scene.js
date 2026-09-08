/*
 * Coordn8 — EXAMPLE SCENE, written the safe way.
 *
 * This is the reference for authoring a video scene against
 * `scripts/render/scene_runtime.js`. Every pattern here exists because the
 * opposite pattern shipped a real defect with every automated gate green
 * (PLAYBOOK Phase F). Copy this shape.
 *
 * Run it two ways:
 *   node examples/example_scene.js trace --out _build/trace_example.json
 *       -> writes the instrumentation trace the semantic gates read (no browser)
 *   (in a browser) include scene_runtime.js + this file, then renderFrame(t)
 *
 * Then gate it:
 *   python3 scripts/gates/check_semantics.py _build/trace_example.json
 *   python3 scripts/gates/check_density.py trace _build/trace_example.json
 */
'use strict';

const path = require('path');
const C = require(path.join(__dirname, '..', 'scripts', 'render', 'scene_runtime.js'));

// ---------------------------------------------------------------------------
// THE SCRIPT. In a real build these lines and their timings come from the TTS
// speech marks (PLAYBOOK 2.3) — never estimated, never hand-typed.
//
// NOTE THE TWO DECLARATIONS THAT MATTER:
//   `name`      — a STABLE beat name. Scenes address beats by this name, never by
//                 index, so deleting or reordering a line is a loud resolve error
//                 instead of six silent mistimings (D2).
//   `subjectId` — WHICH instance/case this line is about, so an asset showing a
//                 different case is caught (D4/S5) rather than shipping as a
//                 confident visual that contradicts the voice.
//   `speech`    — OPTIONAL spoken form for TTS only (PLAYBOOK 2.2c): the caption
//                 and page read "SLA", the voice says the letters.
// ---------------------------------------------------------------------------
const SCRIPT = [
  { name: 'the-problem', subjectId: 'order-9f2', start: 0.0, end: 3.4,
    text: 'Every order that arrives has to be classified before anything else can happen.' },
  { name: 'the-router', subjectId: 'order-9f2', start: 3.6, end: 7.2,
    text: 'The router reads the order shape and picks exactly one handler.' },
  { name: 'the-worked-example', subjectId: 'order-9f2', start: 7.4, end: 11.8,
    text: 'Take this order: it has no shipping address, so it lands on the review path.',
    speech: 'Take this order: it has no shipping address, so it lands on the review path.' },
  { name: 'the-payoff', subjectId: 'order-9f2', start: 12.0, end: 15.5,
    text: 'That single decision is what keeps the rest of the pipeline simple.' },
];

// ---------------------------------------------------------------------------
// THE DRAW FUNCTION — a PURE FUNCTION OF t. This is the crash-safe render
// contract (PLAYBOOK 2.5/R.3): any frame re-renders in isolation, so a killed
// capture loses one frame, not a scene.
// ---------------------------------------------------------------------------
function draw(z) {
  // A standing title. Text drawn with no container is unbounded by nature; keep
  // such text short and positioned by hand.
  z.text({ text: 'How an order gets routed', x: 120, y: 70, size: 46, alpha: 1,
           weight: 700 });

  // ---- SECTION 1: the mental model ------------------------------------
  // `section()` is what makes S7 (declared-but-empty) and S8 (ink regression vs
  // the last approved cut) possible. A section that draws nothing while the
  // narration describes it is exactly the "whole section is blank" defect.
  z.section('mental-model', (s) => {
    // Nodes are DECLARED with ids so connectors can be direction-checked.
    const y = 260;
    s.node({ id: 'order', x: 140, y, w: 260, h: 110, alpha: s.beatAlpha('the-problem'),
             text: 'Order arrives' });
    s.node({ id: 'router', x: 620, y, w: 260, h: 110, alpha: s.beatAlpha('the-router'),
             text: 'Router' });
    s.node({ id: 'handler', x: 1100, y, w: 260, h: 110,
             alpha: s.beatAlpha('the-router', { lead: 0.25 }), text: 'One handler' });
    s.node({ id: 'review', x: 1100, y: y + 190, w: 260, h: 110,
             alpha: s.beatAlpha('the-worked-example'), text: 'Review path' });

    // Connectors declare from/to BY NODE ID. The runtime routes edge-to-edge and
    // draws the arrowhead at the declared `to` end, so the picture can never
    // disagree with the declaration (D5). Direction is checked by S6.
    s.connector({ from: 'order', to: 'router', alpha: s.beatAlpha('the-router') });
    s.connector({ from: 'router', to: 'handler', alpha: s.beatAlpha('the-router', { lead: 0.25 }) });
    s.connector({ from: 'router', to: 'review',
                  alpha: s.beatAlpha('the-worked-example') });
  });

  // ---- SECTION 2: the worked example ----------------------------------
  z.section('worked-example', (s) => {
    // A container. Text drawn inside it is WIDTH-FITTED to it automatically —
    // bounded text is the DEFAULT, so a long string shrinks/ellipsizes instead of
    // spilling out both sides (D3). Overflowing requires `unbounded: true`.
    s.card({ id: 'example-card', x: 140, y: 620, w: 900, h: 260,
             alpha: s.beatAlpha('the-worked-example'), pad: 28 }, (k) => {
      // No `alpha` here: children INHERIT the card's alpha, so the card and its
      // contents fade as one and the timing expression lives in exactly one place.
      k.text({ text: 'Order 9f2 — no shipping address on file', size: 34, y: 660 });
      k.text({ text: 'Because the address is missing, the shape check fails and the router '
                     + 'sends it to the review path instead of fulfilment.',
               // 30px: the legibility floor is 28px at 1080p, and the quality
               // scorecard flagged this very line when it was smaller.
               size: 30, y: 720 });
    });

    // An asset MUST declare subjectId — which case it actually shows. If this said
    // 'order-7c1' while the beat is about 'order-9f2', S5 fails the build (D4).
    // (No `image` is passed here, so nothing paints in a browser; the draw is still
    // recorded for the gates, which is what lets this run headless.)
    s.asset({ path: 'shots/order-9f2-review.png', subjectId: 'order-9f2',
              x: 1100, y: 620, w: 660, h: 260,
              alpha: s.beatAlpha('the-worked-example', { lead: 0.2 }),
              caption: 'the order in the review queue' });
  });

  // ---- SECTION 3: the payoff -------------------------------------------
  z.section('payoff', (s) => {
    const a = s.beatAlpha('the-payoff');
    // Chips spread across the lower frame. Filling the frame is not decoration:
    // a band at 0.0% ink under live narration is the measured "low-effort" look
    // that DEN catches (D7).
    ['one decision', 'one handler', 'no branching downstream'].forEach((label, i) => {
      s.chip({ text: label, x: 160 + i * 380, y: 940, alpha: a, size: 28 });
    });
  });

  // ---- THE FRAME-USE LESSON (D7), demonstrated ------------------------
  // The density gate FAILED an earlier version of this very example: on the first
  // beat, everything lived in the top third and the bottom ~540px sat at 0.0% ink
  // while the narration was speaking. That is precisely the "machine-generated /
  // effortless" look a reviewer names — and it is invisible to every correctness
  // gate, which is why DEN exists.
  //
  // The fix is not filler. It is giving the OPENING beat its own real content:
  // the question the scene is about to answer, stated large in the space the
  // diagram will later occupy. It earns its place (it sets up the curiosity gap),
  // and it means no beat is ever mostly-empty under live narration.
  z.section('opening-frame-use', (s) => {
    // Fades OUT as the WORKED EXAMPLE arrives — not as early as 'the-router',
    // because the density ranking showed that handing the router beat an empty
    // lower frame made it the weakest beat in the scene. Acting on the ranking
    // (not just the pass/fail) is the point: the lowest-ink beat is what a
    // reviewer names next, whether or not it tripped the threshold.
    // `until` resolves BY NAME, so if that beat is renamed or removed this throws
    // instead of silently mistiming.
    const a = s.beatAlpha('the-problem', { until: 'the-router', fadeOut: 0.45 });
    // Sits in the band the diagram has not reached yet, and clears BEFORE the
    // worked-example card appears — the quality scorecard flagged the overlap when
    // these two were on screen together.
    s.card({ id: 'opening-q', x: 140, y: 470, w: 1620, h: 300, alpha: a * 0.55,
             pad: 48 }, (k) => {
      k.text({ text: 'Which handler takes this order — and why?',
               size: 58, weight: 700, y: 540 });
      k.text({ text: 'One decision, made once, up front.', size: 34, y: 640 });
    });
  });
}

// ---------------------------------------------------------------------------
// CLI: emit the instrumentation trace the semantic gates read.
// ---------------------------------------------------------------------------
if (require.main === module) {
  const fs = require('fs');
  const args = process.argv.slice(2);
  const cmd = args[0] || 'trace';
  const oi = args.indexOf('--out');
  const out = oi >= 0 ? args[oi + 1] : '_build/trace_example.json';
  if (cmd !== 'trace') {
    console.error('usage: node examples/example_scene.js trace [--out path.json]');
    process.exit(2);
  }
  const trace = C.buildTrace({ name: 'example', script: SCRIPT, grid: 5, draw });
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(trace, null, 2) + '\n');
  console.error(`[example] wrote ${out} — ${trace.frames.length} frames, `
    + `sections: ${trace.declaredSections.join(', ')}`);
}

module.exports = { SCRIPT, draw };
