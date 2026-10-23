// SRC: tests/v2/fixtures/scenes/neg_random_at_load.js — NEGATIVE fixture: randomness read once at module load
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([{ name: 'one', text: 'The offset was rolled once when the file loaded.' }]);
const OFFSET = Math.round(Math.random() * 1e6) / 1e3;   // the defect: outside the draw, so only a re-run shows it

function draw(z) {
  S.checklist(z, { enter: 'one', items: [{ label: 'Offset', state: 'open' }] });
  z.chip({ text: 'offset', x: 400 + OFFSET, y: 700, alpha: 1 });
}

module.exports = { name: 'neg_random_at_load', script, words, draw };
