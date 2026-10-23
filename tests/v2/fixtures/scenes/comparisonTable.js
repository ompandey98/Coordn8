// SRC: tests/v2/fixtures/scenes/comparisonTable.js — synthetic fixture for the 'comparisonTable' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "options",
    "text": "Compare the three queue options."
  },
  {
    "name": "rows",
    "text": "Look at ordering, cost and replay."
  },
  {
    "name": "pick",
    "text": "The log wins on replay."
  }
]);

function draw(z) {
  S.comparisonTable(z, {
    title: 'Three queue options', enter: 'options',
    columns: ['Simple queue', 'Topic', 'Log'],
    rows: [{ label: 'Ordering', cells: ['none', 'per key', 'per partition'], at: 'rows' },
           { label: 'Cost', cells: ['low', 'medium', 'medium'], at: 'rows' },
           { label: 'Replay', cells: ['no', 'no', 'yes'], at: 'rows' }],
    highlight: { column: 'Log', at: 'pick' },
  });
}

module.exports = { name: 'comparisonTable', script, words, draw };
