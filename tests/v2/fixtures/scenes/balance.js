// SRC: tests/v2/fixtures/scenes/balance.js — synthetic fixture for the 'balance' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "two-ways",
    "text": "There are two ways to ship this change."
  },
  {
    "name": "weigh",
    "text": "Weigh the rollback cost against the time saved."
  },
  {
    "name": "verdict",
    "text": "The flag wins because rollback is nearly free."
  }
]);

function draw(z) {
  S.balance(z, {
    title: 'Feature flag or full deploy', enter: 'two-ways', tiltAt: 'weigh',
    left: { label: 'Full deploy', weight: 3, note: 'hours to roll back' },
    right: { label: 'Feature flag', weight: 8, note: 'seconds to roll back' },
    verdict: { text: 'the flag carries the decision', at: 'verdict' },
  });
}

module.exports = { name: 'balance', script, words, draw };
