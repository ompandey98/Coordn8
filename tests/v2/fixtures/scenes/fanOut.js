// SRC: tests/v2/fixtures/scenes/fanOut.js — synthetic fixture for the 'fanOut' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "event",
    "text": "One order event is published."
  },
  {
    "name": "consumers",
    "text": "Billing, shipping and email each receive a copy."
  }
]);

function draw(z) {
  S.fanOut(z, {
    title: 'One event, many readers', enter: 'event',
    source: { id: 'ev', label: 'Order placed' },
    targets: [{ id: 'bill', label: 'Billing', at: 'consumers' }, { id: 'ship', label: 'Shipping', at: 'consumers' },
              { id: 'mail', label: 'Email', at: 'consumers' }],
  });
}

module.exports = { name: 'fanOut', script, words, draw };
