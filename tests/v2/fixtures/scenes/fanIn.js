// SRC: tests/v2/fixtures/scenes/fanIn.js — synthetic fixture for the 'fanIn' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "sources",
    "text": "Three feeds report the same metric."
  },
  {
    "name": "merge",
    "text": "The aggregator merges them into one series."
  }
]);

function draw(z) {
  S.fanIn(z, {
    title: 'Three feeds, one series', enter: 'sources', note: 'one value per minute',
    sources: [{ id: 'a', label: 'Region east' }, { id: 'b', label: 'Region west' }, { id: 'c', label: 'Region south' }],
    sink: { id: 'agg', label: 'Aggregator', at: 'merge' },
  });
}

module.exports = { name: 'fanIn', script, words, draw };
