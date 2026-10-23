// SRC: tests/v2/fixtures/scenes/pipeline.js — synthetic fixture for the 'pipeline' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "ingest",
    "text": "Raw files land in the ingest stage."
  },
  {
    "name": "clean",
    "text": "Cleaning drops broken rows."
  },
  {
    "name": "score",
    "text": "Scoring adds a risk value."
  },
  {
    "name": "publish",
    "text": "Publishing writes the final table."
  }
]);

function draw(z) {
  S.pipeline(z, {
    title: 'The scoring pipeline', enter: 'ingest',
    stages: [{ id: 'in', label: 'Ingest', at: 'ingest', note: 'files arrive every hour' },
             { id: 'clean', label: 'Clean', at: 'clean', note: 'rows with missing ids are dropped' },
             { id: 'score', label: 'Score', at: 'score', note: 'each row gets a risk value from 0 to 1' },
             { id: 'pub', label: 'Publish', at: 'publish', note: 'the table readers query' }],
  });
}

module.exports = { name: 'pipeline', script, words, draw };
