// SRC: tests/v2/fixtures/scenes/failCard.js — synthetic fixture for the 'failCard' scene component (data/scene_types.json)
'use strict';
const { build, COMPONENTS } = require('./_util.js');
const S = require(COMPONENTS);

const { script, words } = build([
  {
    "name": "boom",
    "text": "The nightly import failed at the schema check."
  },
  {
    "name": "why",
    "text": "A new column arrived that the schema did not allow."
  },
  {
    "name": "fix",
    "text": "Allow the column, then re-run the import."
  }
]);

function draw(z) {
  S.failCard(z, {
    title: 'Why the import stopped', enter: 'boom',
    error: 'SchemaError: unexpected column "region"', where: 'import/validate.py:88',
    cause: { text: 'The upstream export added a region column that the schema rejects.', at: 'why' },
    fix: { text: 'Add region to the allowed columns and re-run the import job.', at: 'fix' },
  });
}

module.exports = { name: 'failCard', script, words, draw };
