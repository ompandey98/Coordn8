// SRC: tests/v2/fixtures/scenes/zoomDetail.js — synthetic fixture for the 'zoomDetail' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "whole",
    "text": "This is the whole dashboard.",
    "subjectId": "dash"
  },
  {
    "name": "zoom",
    "text": "Zoom into the error panel in the corner.",
    "subjectId": "dash"
  },
  {
    "name": "read",
    "text": "It counts failed payments in the last hour.",
    "subjectId": "dash"
  }
]);

function draw(z) {
  S.zoomDetail(z, {
    title: 'The panel that matters', enter: 'whole', at: 'zoom',
    overview: { label: 'Operations dashboard', asset: { path: 'shots/ops-dashboard.png', subjectId: 'dash', srcW: 1200 } },
    region: { x: 0.62, y: 0.05, w: 0.33, h: 0.3 },
    detail: { label: 'Failed payments', lines: ['counted over the last hour', 'red above 50'], lineAt: ['zoom', 'read'] },
  });
}

module.exports = { name: 'zoomDetail', script, words, draw, subsystem: 'ops', assets: ['shots/ops-dashboard.png'] };
