// SRC: tests/v2/fixtures/scenes/evidenceCard.js — synthetic fixture for the 'evidenceCard' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "claim",
    "text": "The timeout is set in exactly one place.",
    "subjectId": "timeout"
  },
  {
    "name": "proof",
    "text": "Here is the line, and the dashboard that shows it.",
    "subjectId": "timeout"
  }
]);

function draw(z) {
  S.evidenceCard(z, {
    title: 'Where the timeout comes from', enter: 'claim', at: 'claim',
    quote: 'Requests are cancelled after thirty seconds unless a caller overrides the deadline.',
    source: 'client/config.py:41',
    asset: { path: 'shots/timeout-dashboard.png', subjectId: 'timeout', srcW: 1400, srcH: 900, at: 'proof',
             caption: 'p99 flattens at 30 s' },
  });
}

module.exports = { name: 'evidenceCard', script, words, draw, subsystem: 'client', assets: ['shots/timeout-dashboard.png'] };
