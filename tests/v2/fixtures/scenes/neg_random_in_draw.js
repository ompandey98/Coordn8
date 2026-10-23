// SRC: tests/v2/fixtures/scenes/neg_random_in_draw.js — NEGATIVE fixture: unseeded randomness inside the draw
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([{ name: 'one', text: 'A jittery chip that moves on every render.' }]);

function draw(z) {
  S.checklist(z, { enter: 'one', items: [{ label: 'Jitter', state: 'open' }] });
  z.chip({ text: 'jitter', x: 400 + 40 * Math.random(), y: 700, alpha: 1 });   // the defect
}

module.exports = { name: 'neg_random_in_draw', script, words, draw };
