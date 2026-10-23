// SRC: tests/v2/fixtures/scenes/timeline.js — synthetic fixture for the 'timeline' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "t0",
    "text": "At nine the deploy started."
  },
  {
    "name": "t1",
    "text": "Errors climbed at nine twelve."
  },
  {
    "name": "t2",
    "text": "The rollback finished at nine thirty."
  }
]);

function draw(z) {
  S.timeline(z, {
    title: 'The incident, minute by minute', enter: 't0', axisLabel: 'morning, local time',
    events: [{ when: '09:00', label: 'Deploy starts' },
             { when: '09:12', label: 'Error rate climbs', at: 't1' },
             { when: '09:21', label: 'Rollback begins', at: 't2' },
             { when: '09:30', label: 'Service recovered', at: { line: 't2', word: 'thirty' } }],
  });
}

module.exports = { name: 'timeline', script, words, draw };
