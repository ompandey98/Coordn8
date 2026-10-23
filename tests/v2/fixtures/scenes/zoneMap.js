// SRC: tests/v2/fixtures/scenes/zoneMap.js — synthetic fixture for the 'zoneMap' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "map",
    "text": "The system splits into public and private zones."
  },
  {
    "name": "boundary",
    "text": "Only the gateway crosses the trust boundary."
  }
]);

function draw(z) {
  S.zoneMap(z, {
    title: 'Trust zones', enter: 'map', grid: { cols: 4, rows: 3 },
    zones: [{ id: 'web', label: 'Public web', note: 'anyone', col: 0, row: 0, cols: 2 },
            { id: 'cdn', label: 'CDN', col: 2, row: 0, cols: 2 },
            { id: 'gw', label: 'Gateway', note: 'checks tokens', col: 0, row: 1, cols: 4 },
            { id: 'svc', label: 'Services', col: 0, row: 2, cols: 2, at: 'boundary' },
            { id: 'db', label: 'Database', col: 2, row: 2, cols: 2, at: 'boundary' }],
    boundary: { zones: ['svc', 'db'], label: 'private network', at: 'boundary' },
    marker: { zone: 'gw', at: 'boundary', label: 'the only crossing' },
  });
}

module.exports = { name: 'zoneMap', script, words, draw };
