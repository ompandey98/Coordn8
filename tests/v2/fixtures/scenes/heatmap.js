// SRC: tests/v2/fixtures/scenes/heatmap.js — synthetic fixture for the 'heatmap' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "grid",
    "text": "Errors per hour, for each region."
  },
  {
    "name": "hot",
    "text": "West at noon is the hot spot."
  }
]);

function draw(z) {
  S.heatmap(z, {
    title: 'Errors by region and hour', enter: 'grid', legend: 'stronger = more errors',
    rows: ['East', 'West', 'South'], cols: ['09', '10', '11', '12', '13', '14'],
    values: [[2, 3, 2, 4, 3, 2], [3, 5, 8, 14, 9, 4], [1, 1, 2, 3, 2, 1]],
    highlight: { row: 1, col: 3, at: 'hot' },
  });
}

module.exports = { name: 'heatmap', script, words, draw };
