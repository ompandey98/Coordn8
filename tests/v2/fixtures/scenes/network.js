// SRC: tests/v2/fixtures/scenes/network.js — synthetic fixture for the 'network' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "nodes",
    "text": "Five services call each other."
  },
  {
    "name": "edges",
    "text": "Orders sits in the middle of almost every call."
  }
]);

function draw(z) {
  S.network(z, {
    title: 'Who calls whom', enter: 'nodes', hub: 'orders', seed: 11,
    nodes: [{ id: 'orders', label: 'Orders' }, { id: 'users', label: 'Users' }, { id: 'pay', label: 'Payments' },
            { id: 'stock', label: 'Stock' }, { id: 'mail', label: 'Email' }],
    edges: [{ from: 'users', to: 'orders', at: 'edges' }, { from: 'orders', to: 'pay', at: 'edges' },
            { from: 'orders', to: 'stock', at: 'edges' }, { from: 'orders', to: 'mail', at: 'edges' },
            { from: 'pay', to: 'mail', at: 'edges' }],
  });
}

module.exports = { name: 'network', script, words, draw };
