// SRC: tests/v2/fixtures/scenes/layerStack.js — synthetic fixture for the 'layerStack' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "top",
    "text": "A request enters at the edge."
  },
  {
    "name": "down",
    "text": "It passes auth, then the service, then storage."
  },
  {
    "name": "focus",
    "text": "Auth is where most latency hides."
  }
]);

function draw(z) {
  S.layerStack(z, {
    title: 'The layers a request crosses', enter: 'top', focus: { layer: 'auth', at: 'focus' },
    layers: [{ id: 'edge', label: 'Edge proxy', note: 'TLS and routing' },
             { id: 'auth', label: 'Auth', note: 'token check on every call', at: 'down' },
             { id: 'svc', label: 'Service', note: 'business rules', at: 'down' },
             { id: 'store', label: 'Storage', note: 'rows and blobs', at: 'down' }],
  });
}

module.exports = { name: 'layerStack', script, words, draw };
