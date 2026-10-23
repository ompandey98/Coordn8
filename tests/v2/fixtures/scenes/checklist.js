// SRC: tests/v2/fixtures/scenes/checklist.js — synthetic fixture for the 'checklist' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "list",
    "text": "Before release, four checks must pass."
  },
  {
    "name": "status",
    "text": "Three pass and the load test still fails."
  }
]);

function draw(z) {
  S.checklist(z, {
    title: 'Release checklist', enter: 'list',
    items: [{ label: 'Unit tests green', state: 'done' }, { label: 'Migration rehearsed', state: 'done' },
            { label: 'Docs updated', state: 'done' }, { label: 'Load test at 2x traffic', state: 'fail', at: 'status' }],
  });
}

module.exports = { name: 'checklist', script, words, draw };
