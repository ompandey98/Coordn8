// SRC: tests/v2/fixtures/scenes/funnel.js — synthetic fixture for the 'funnel' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "visits",
    "text": "A thousand people open the sign-up page."
  },
  {
    "name": "form",
    "text": "Four hundred start the form."
  },
  {
    "name": "verify",
    "text": "Only ninety finish email verification."
  }
]);

function draw(z) {
  S.funnel(z, {
    title: 'Where sign-ups are lost', enter: 'visits',
    stages: [{ label: 'Opened page', value: 1000 },
             { label: 'Started form', value: 400, at: 'form' },
             { label: 'Verified email', value: 90, at: { line: 'verify', word: 'ninety' } }],
  });
}

module.exports = { name: 'funnel', script, words, draw };
