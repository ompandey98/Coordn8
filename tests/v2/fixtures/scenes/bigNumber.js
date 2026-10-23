// SRC: tests/v2/fixtures/scenes/bigNumber.js — synthetic fixture for the 'bigNumber' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "setup",
    "text": "How many requests does one node absorb?"
  },
  {
    "name": "reveal",
    "text": "Twelve thousand per second, at the limit."
  }
]);

function draw(z) {
  S.bigNumber(z, {
    title: 'Capacity of one node', enter: 'setup', value: 12000, unit: 'req/s',
    label: 'requests per second before latency doubles', at: { line: 'reveal', word: 'thousand' },
    context: [{ text: 'measured on the load rig', at: 'reveal' }, { text: 'four vCPU', at: 'reveal' }],
  });
}

module.exports = { name: 'bigNumber', script, words, draw };
