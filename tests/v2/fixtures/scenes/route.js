// SRC: tests/v2/fixtures/scenes/route.js — synthetic fixture for the 'route' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "origin",
    "text": "A request leaves the browser."
  },
  {
    "name": "travel",
    "text": "It passes the gateway and reaches the billing service."
  },
  {
    "name": "arrive",
    "text": "Billing answers in one round trip."
  }
]);

function draw(z) {
  S.route(z, {
    title: 'One request, end to end', enter: 'origin',
    from: { id: 'browser', label: 'Browser' }, via: [{ id: 'gateway', label: 'Gateway', at: 'travel' }],
    to: { id: 'billing', label: 'Billing service', at: { line: 'travel', word: 'billing' } },
    at: { line: 'travel', word: 'passes' }, summary: 'Browser → Gateway → Billing',
  });
}

module.exports = { name: 'route', script, words, draw };
