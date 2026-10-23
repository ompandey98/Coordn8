// SRC: tests/v2/fixtures/scenes/treeReveal.js — synthetic fixture for the 'treeReveal' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "root",
    "text": "Every refund request starts with one question."
  },
  {
    "name": "branch",
    "text": "Is it within thirty days?"
  },
  {
    "name": "leaves",
    "text": "Late requests go to a person; others pay out."
  }
]);

function draw(z) {
  S.treeReveal(z, {
    title: 'How a refund is decided', enter: 'root',
    root: { id: 'req', label: 'Refund request', children: [
      { id: 'recent', label: 'Within 30 days', when: 'yes', at: 'branch', children: [
        { id: 'auto', label: 'Pay out', when: 'amount small', at: 'leaves', outcome: 'positive' },
        { id: 'check', label: 'Check receipt', when: 'amount large', at: 'leaves' }] },
      { id: 'late', label: 'Older', when: 'no', at: 'branch', children: [
        { id: 'human', label: 'Person reviews', when: 'always', at: 'leaves', outcome: 'warning' }] }] },
  });
}

module.exports = { name: 'treeReveal', script, words, draw };
