// SRC: tests/v2/fixtures/freedom/invented_tide_ladder.js — an INVENTED scene form (not in data/scene_types.json)
'use strict';
/*
 * "Tide ladder": each retry is a rung, and the wait before it is drawn as a tide that rises
 * rung by rung, so the doubling is seen as height, not read as numbers. No catalog scene fits
 * (a timeline flattens the doubling into equal steps; a bar chart loses the order of the
 * attempts), so it is drawn straight from the runtime primitives and declared as invented,
 * with its reason. It must pass the same outcome gates as any catalog component.
 */
const { build } = require('../scenes/_util.js');

const { script, words } = build([
  { name: 'rungs', text: 'Each failed call climbs one rung of the ladder.' },
  { name: 'wait', text: 'Every rung waits twice as long as the one before.' },
  { name: 'tide', text: 'So the tide of waiting rises fast, and the fourth try waits eight seconds.' },
]);

const RUNGS = [
  { id: 'try1', label: 'Try 1 · wait 1 s', wait: 1 },
  { id: 'try2', label: 'Try 2 · wait 2 s', wait: 2 },
  { id: 'try3', label: 'Try 3 · wait 4 s', wait: 4 },
  { id: 'try4', label: 'Try 4 · wait 8 s', wait: 8 },
];

function draw(z) {
  z.section('tide-ladder', (q) => {
    q.declareForm({ invented: 'tide-ladder',
      reason: 'retries whose waits double read as rising height; a timeline would show equal steps' });
    const base = q.beatAlpha('rungs');
    q.text({ text: 'Retries climb a ladder of longer waits', x: 96, y: 54, size: 46, weight: 700, alpha: base });
    RUNGS.forEach((r, i) => {
      q.node({ id: r.id, x: 120, y: 220 + i * 200, w: 420, h: 120, alpha: base, text: r.label });
      if (i > 0) q.connector({ from: RUNGS[i - 1].id, to: r.id, alpha: base });
    });
    const rise = q.beatProgress('wait', { dur: 2.4 });
    q.card({ id: 'tide', x: 700, y: 220, w: 1100, h: 720, alpha: base, pad: 28 }, (k) => {
      k.text({ text: 'Seconds of waiting before each try', size: 30, y: 250 });
    });
    RUNGS.forEach((r, i) => {
      const full = 540 * (r.wait / 8);
      const h = Math.max(8, full * rise);
      q.mark({ shape: 'rect', x: 760 + i * 250, y: 900 - h, w: 180, h, radius: 10, alpha: base,
               fill: '#9a4694', fillOpacity: 0.6, meaning: `the wait before try ${i + 1}` });
    });
    q.chip({ text: 'waits double: 1, 2, 4, 8 s', x: 120, y: 980, alpha: q.beatAlpha('tide'), size: 30 });
  });
}

module.exports = { name: 'invented_tide_ladder', script, words, draw };
