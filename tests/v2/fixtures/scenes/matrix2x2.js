// SRC: tests/v2/fixtures/scenes/matrix2x2.js — synthetic fixture for the 'matrix2x2' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "frame",
    "text": "Every caching choice trades freshness against speed."
  },
  {
    "name": "corners",
    "text": "Fast and fresh is the corner everyone wants."
  },
  {
    "name": "pick",
    "text": "This service picks fast but slightly stale reads."
  }
]);

function draw(z) {
  S.matrix2x2(z, {
    title: 'Caching trade-off', enter: 'frame',
    xAxis: ['slower', 'faster'], yAxis: ['staler', 'fresher'],
    cells: [[{ label: 'Read-through', note: 'always fresh, every read pays the trip', at: 'corners' },
             { label: 'Ideal', note: 'fresh and fast: rarely possible', at: 'corners' }],
            [{ label: 'No cache', note: 'slow and only as fresh as the source' },
             { label: 'TTL cache', note: 'fast reads, up to a minute stale', at: 'pick' }]],
    highlight: { row: 1, col: 1, at: 'pick' },
  });
}

module.exports = { name: 'matrix2x2', script, words, draw };
