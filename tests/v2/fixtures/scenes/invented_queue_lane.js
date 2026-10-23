// SRC: tests/v2/fixtures/scenes/invented_queue_lane.js — an INVENTED scene form (not in data/scene_types.json)
'use strict';
/*
 * "Queue lane": requests wait in a lane and leave through one gate; the lane visibly drains
 * as the narration says the worker catches up. No catalog entry fits (it is neither a
 * pipeline nor a funnel), so it is drawn straight from the runtime primitives and declared
 * as invented, with a reason. It must pass the same outcome gates as any component.
 */
const { build } = require('./_util.js');

const { script, words } = build([
  { name: 'queue', text: 'Requests pile up in the waiting lane.' },
  { name: 'gate', text: 'One worker lets them through the gate.' },
  { name: 'drain', text: 'As it catches up, the lane drains.' },
]);

function draw(z) {
  z.section('queue-lane', (q) => {
    q.declareForm({ invented: 'queue-lane', reason: 'waiting work that drains through one gate is neither a pipeline nor a funnel' });
    const base = q.beatAlpha('queue');
    q.text({ text: 'Requests waiting for one worker', x: 96, y: 54, size: 46, weight: 700, alpha: base });
    q.card({ id: 'lane', x: 96, y: 300, w: 1200, h: 220, alpha: base, pad: 24 }, (k) => {
      k.text({ text: 'Waiting lane', size: 30, y: 324 });
    });
    q.node({ id: 'gate', x: 1420, y: 330, w: 300, h: 160, alpha: q.beatAlpha('gate'), text: 'Worker' });
    q.node({ id: 'out', x: 1420, y: 640, w: 300, h: 110, alpha: q.beatAlpha('gate'), text: 'Done' });
    q.connector({ from: 'gate', to: 'out', alpha: q.beatAlpha('gate') });
    // seeded spacing so the lane looks hand-placed, identically on every frame and every run
    const rng = q.random('queue-lane', 'spacing');
    const drain = q.beatProgress('drain', { dur: 2.0 });
    const n = 8, left = Math.round(n * (1 - drain));
    for (let i = 0; i < n; i++) {
      const x = 140 + i * 130 + rng.range(-12, 12);
      q.mark({ shape: 'rect', x, y: 400, w: 90, h: 90, radius: 14, alpha: i < left ? base : 0,
               fill: '#7cc4ff', fillOpacity: 0.55, meaning: 'one waiting request' });
    }
    q.chip({ text: `${left} waiting`, x: 96, y: 600, alpha: base, size: 30 });
    q.card({ id: 'note', x: 96, y: 700, w: 1200, h: 130, alpha: q.beatAlpha('drain'), pad: 28 }, (k) => {
      k.text({ text: 'One gate sets the pace: the lane only drains as fast as the worker.', size: 30, y: 742 });
    });
  });
}

module.exports = { name: 'invented_queue_lane', script, words, draw };
