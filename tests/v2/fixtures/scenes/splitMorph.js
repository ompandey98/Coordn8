// SRC: tests/v2/fixtures/scenes/splitMorph.js — synthetic fixture for the 'splitMorph' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "before",
    "text": "Before the change every page queried the database."
  },
  {
    "name": "after",
    "text": "After it, pages read from a shared cache."
  }
]);

function draw(z) {
  S.splitMorph(z, {
    title: 'What the cache changed', enter: 'before',
    before: { label: 'Before', items: ['Every page hits the database', 'p95 latency 900 ms', 'Load grows with traffic'] },
    after: { label: 'After', items: ['Pages read the cache', 'p95 latency 120 ms', 'Load stays flat'], at: 'after' },
  });
}

module.exports = { name: 'splitMorph', script, words, draw };
