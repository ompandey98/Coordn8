// SRC: tests/v2/fixtures/scenes/stateRing.js — synthetic fixture for the 'stateRing' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "born",
    "text": "An order starts life as created."
  },
  {
    "name": "paid",
    "text": "Payment moves it to paid."
  },
  {
    "name": "shipped",
    "text": "The warehouse marks it shipped."
  },
  {
    "name": "closed",
    "text": "Delivery closes the loop."
  }
]);

function draw(z) {
  S.stateRing(z, {
    title: 'Lifecycle of one order', enter: 'born', center: 'one order',
    states: [{ id: 'created', label: 'Created' }, { id: 'paid', label: 'Paid', at: 'paid' },
             { id: 'shipped', label: 'Shipped', at: 'shipped' }, { id: 'delivered', label: 'Delivered', at: 'closed' }],
    current: [{ state: 'created', at: 'born' }, { state: 'paid', at: 'paid' },
              { state: 'shipped', at: 'shipped' }, { state: 'delivered', at: 'closed' }],
  });
}

module.exports = { name: 'stateRing', script, words, draw };
