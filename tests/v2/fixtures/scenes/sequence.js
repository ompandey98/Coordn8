// SRC: tests/v2/fixtures/scenes/sequence.js — synthetic fixture for the 'sequence' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "ask",
    "text": "The app asks the API for a token."
  },
  {
    "name": "check",
    "text": "The API checks the user store."
  },
  {
    "name": "reply",
    "text": "A token comes back to the app."
  }
]);

function draw(z) {
  S.sequence(z, {
    title: 'Getting a token', enter: 'ask',
    actors: [{ id: 'app', label: 'App' }, { id: 'api', label: 'API' }, { id: 'users', label: 'User store' }],
    messages: [{ from: 'app', to: 'api', label: 'POST /token', at: 'ask' },
               { from: 'api', to: 'users', label: 'look up user', at: 'check' },
               { from: 'users', to: 'api', label: 'user found', at: 'check', reply: true },
               { from: 'api', to: 'app', label: 'signed token', at: 'reply', reply: true }],
  });
}

module.exports = { name: 'sequence', script, words, draw };
