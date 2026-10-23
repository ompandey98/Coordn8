// SRC: tests/v2/fixtures/scenes/gauge.js — synthetic fixture for the 'gauge' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "measure",
    "text": "Disk use is the number to watch."
  },
  {
    "name": "limit",
    "text": "It now sits above the eighty percent alarm."
  }
]);

function draw(z) {
  S.gauge(z, {
    title: 'Disk use against the alarm', enter: 'measure', value: 86, min: 0, max: 100, threshold: 80,
    format: 'pct', label: 'of the data volume in use', at: { line: 'limit', word: 'above' },
  });
}

module.exports = { name: 'gauge', script, words, draw };
