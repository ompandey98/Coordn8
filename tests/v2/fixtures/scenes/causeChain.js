// SRC: tests/v2/fixtures/scenes/causeChain.js — synthetic fixture for the 'causeChain' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "cause",
    "text": "A config typo set the pool size to one."
  },
  {
    "name": "chain",
    "text": "Requests queued, timeouts fired, retries piled on."
  },
  {
    "name": "effect",
    "text": "The checkout page went down."
  }
]);

function draw(z) {
  S.causeChain(z, {
    title: 'How one typo took checkout down', enter: 'cause',
    links: [{ id: 'typo', label: 'Pool size = 1' }, { id: 'queue', label: 'Requests queue', at: 'chain' },
            { id: 'timeout', label: 'Timeouts fire', at: 'chain' }, { id: 'retry', label: 'Retries pile on', at: 'chain' },
            { id: 'down', label: 'Checkout down', at: 'effect' }],
  });
}

module.exports = { name: 'causeChain', script, words, draw };
