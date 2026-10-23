// SRC: tests/v2/fixtures/scenes/ledger.js — synthetic fixture for the 'ledger' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "start",
    "text": "Each retry adds to the bill."
  },
  {
    "name": "first",
    "text": "The first retry costs two hundred milliseconds."
  },
  {
    "name": "second",
    "text": "The second doubles that to four hundred."
  },
  {
    "name": "third",
    "text": "By the third the user has waited over a second."
  }
]);

function draw(z) {
  S.ledger(z, {
    title: 'Retry cost, counted', enter: 'start', unit: 'ms', totalLabel: 'Extra wait',
    entries: [{ label: 'First retry', delta: 200, at: 'first' },
              { label: 'Second retry', delta: 400, at: 'second' },
              { label: 'Third retry', delta: 800, at: 'third' }],
  });
}

module.exports = { name: 'ledger', script, words, draw };
