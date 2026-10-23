// SRC: tests/v2/fixtures/scenes/codeSpotlight.js — synthetic fixture for the 'codeSpotlight' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "context",
    "text": "The router reads the order shape."
  },
  {
    "name": "line",
    "text": "This one comparison sends it to review."
  }
]);

function draw(z) {
  S.codeSpotlight(z, {
    title: 'The line that decides', enter: 'context', ref: 'router.py:88', firstLine: 85,
    code: ['def route(order):', '    if order.total <= 0:', '        return REJECT',
           '    if not order.address:', '        return REVIEW', '    return FULFIL'],
    focus: { line: 3, at: 'line' },
    explain: { text: 'No address means a person checks the order before anything ships.', at: 'line' },
  });
}

module.exports = { name: 'codeSpotlight', script, words, draw };
