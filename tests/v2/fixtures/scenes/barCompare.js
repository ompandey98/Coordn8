// SRC: tests/v2/fixtures/scenes/barCompare.js — synthetic fixture for the 'barCompare' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "bars",
    "text": "Build times for the four targets."
  },
  {
    "name": "base",
    "text": "Only the web target beats the ten minute budget."
  }
]);

function draw(z) {
  S.barCompare(z, {
    title: 'Build time per target', enter: 'bars', unit: 'min', highlight: 'Web',
    bars: [{ label: 'Web', value: 6 }, { label: 'iOS', value: 14 }, { label: 'Android', value: 12 }, { label: 'Server', value: 11 }],
    baseline: { label: 'budget', value: 10, at: 'base' },
  });
}

module.exports = { name: 'barCompare', script, words, draw };
